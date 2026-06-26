"""Master tick clock — the sole asyncio task that drives the tick system."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class TickClock:
    """Monotonic tick counter synchronized to wall clock.

    Increments at a fixed rate (default 1000 Hz). Measures slip as the
    drift of each tick boundary from its scheduled wall-clock time, expressed
    in whole tick periods.

    The clock runs as the sole framework-driven asyncio.Task and is the only
    writer of self._tick — all reads are lock-free (asyncio single-threaded).

    The clock never awaits hot-path work. At each tick boundary it advances
    the counter, fires the scheduler (which launches due at_tick work as
    tasks), and emits any slip event as a fire-and-forget task. Plugin
    background tasks (via Plugin.create_background_task) run independently.
    """

    def __init__(
        self,
        tick_rate: int,
        scheduler: Any | None = None,
        event_bus: Any | None = None,
        slip_threshold: int = 5,
        precision: str = "sleep",
        busy_wait_us: int = 200,
        catchup: str = "skip",
        time_source: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
    ) -> None:
        """Initialise the tick clock.

        ``time_source`` and ``sleep`` are injectable for deterministic
        testing and simulation only; leave both as ``None`` in production
        to use ``time.monotonic`` and ``asyncio.sleep`` (defaults unchanged).
        """
        self._tick_rate: int = tick_rate
        self._tick_interval: float = 1.0 / tick_rate
        self._tick: int = 0
        self._scheduler: Any = scheduler
        self._event_bus: Any = event_bus
        self._slip_threshold: int = slip_threshold
        self._precision: str = precision
        self._busy_wait_sec: float = busy_wait_us / 1_000_000
        self._catchup: str = catchup
        self._task: asyncio.Task[None] | None = None
        self._running: bool = False
        self._start_wall: float = 0.0
        self._last_slip: int = 0
        self._time: Callable[[], float] = time_source or time.monotonic
        self._sleep: Callable[[float], Awaitable[Any]] = sleep or asyncio.sleep
        # Tracks in-flight slip-event tasks so they are not garbage-collected
        # before completion (asyncio only retains a weak reference).
        self._slip_tasks: set[asyncio.Task[None]] = set()

    @property
    def tick(self) -> int:
        """Current tick number. Lock-free read."""
        return self._tick

    @property
    def slip(self) -> int:
        """Slip of the most recently completed tick boundary, in tick periods."""
        return self._last_slip

    def start(self) -> None:
        """Start the tick loop as a background task. Call from core.start()."""
        if self._running:
            return
        self._running = True
        self._start_wall = self._time()
        self._last_slip = 0
        self._task = asyncio.create_task(self._loop(), name="uxok.tick_clock")

    async def stop(self) -> None:
        """Stop the tick loop. Call from core.stop()."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def _loop(self) -> None:
        """Crash guard around the tick loop.

        If the clock loop crashes, it logs the failure and publishes a
        core.tick_clock_failed event so supervisors can react. There is no
        gate to deactivate or fail — callers no longer block on the clock.
        """
        try:
            await self._run()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.critical(
                "Tick clock crashed at tick %d; tick processing has stopped",
                self._tick,
                exc_info=True,
            )
            self._running = False
            if self._event_bus is not None:
                with contextlib.suppress(Exception):
                    from uxok.protocols import Event

                    await self._event_bus.publish(
                        Event(
                            "core.tick_clock_failed",
                            {"tick": self._tick, "tick_rate": self._tick_rate},
                        )
                    )

    async def _run(self) -> None:
        """Main tick loop body.

        At each tick boundary:
        1. Measure slip (how late we woke up)
        2. Increment tick counter
        3. Fire scheduled operations (launches tasks; does NOT await them)
        4. Emit slip event as a fire-and-forget task if threshold exceeded
        5. Advance boundary (monotonic, no drift accumulation)
        """
        next_boundary = self._start_wall + self._tick_interval
        while self._running:
            now = self._time()
            sleep_for = next_boundary - now

            if sleep_for > 0:
                if self._precision == "hybrid" and sleep_for > self._busy_wait_sec:
                    await self._sleep(sleep_for - self._busy_wait_sec)
                    while self._time() < next_boundary:
                        pass
                else:
                    await self._sleep(sleep_for)

            actual = self._time()
            drift = actual - next_boundary
            self._last_slip = max(0, int(drift / self._tick_interval))

            if self._catchup == "skip" and self._last_slip > 0:
                # Jump over the missed boundaries instead of replaying them.
                # The scheduler fires everything due in the skipped range once
                # (range-based due-collection) and the slip event reports the
                # jump. Burst mode replays each missed tick back-to-back.
                self._tick += self._last_slip
                next_boundary = actual  # re-anchor; += interval below

            self._tick += 1

            if self._scheduler:
                await self._scheduler.tick(self._tick)

            if self._last_slip >= self._slip_threshold and self._event_bus is not None:
                # Emit the slip event as a fire-and-forget task. Bind the
                # boundary's values now so later tick advances don't corrupt
                # the payload. Store the task reference to prevent premature GC.
                _slip_task = asyncio.create_task(
                    self._emit_slip_event(self._tick, self._last_slip),
                    name="uxok.tick_slip_event",
                )
                self._slip_tasks.add(_slip_task)
                _slip_task.add_done_callback(self._slip_tasks.discard)

            next_boundary += self._tick_interval

        logger.debug("Tick clock stopped at tick %d", self._tick)

    async def _emit_slip_event(self, tick: int, slip: int) -> None:
        """Emit a tick slip event through the event bus for the given boundary."""
        from uxok.protocols import Event

        slip_event = Event(
            name="core.tick_slip",
            data={
                "tick": tick,
                "slip": slip,
                "tick_rate": self._tick_rate,
            },
        )
        await self._event_bus.publish(slip_event)
