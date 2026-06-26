"""Deterministic time double for TickClock testing.

FakeTime replaces time.monotonic and asyncio.sleep with a controlled
fake so TickClock can be driven without wall-clock dependencies.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uxok.timing._clock import TickClock


class FakeTime:
    """Controllable fake time source.

    ``monotonic()`` returns the current fake time and adds ``auto_advance``
    per call — this is the escape hatch for TickClock's hybrid busy-spin
    loop ``while self._time() < next_boundary``, which would otherwise spin
    forever on a frozen clock.

    ``sleep(delay)`` records the delay and parks on an asyncio.Future keyed
    by the deadline ``_now + delay``.  Call ``advance(dt)`` to move time
    forward and resolve all sleepers whose deadline has been reached.
    """

    def __init__(self, *, auto_advance: float = 0.0) -> None:
        self._now: float = 0.0
        self._auto_advance: float = auto_advance
        # {deadline: [Future, ...]}  — multiple sleepers at the same deadline
        self._sleepers: dict[float, list[asyncio.Future[None]]] = {}
        self.sleep_calls: list[float] = []

    # ------------------------------------------------------------------
    # Time source interface

    def monotonic(self) -> float:
        """Return current fake time, then advance by auto_advance."""
        t = self._now
        self._now += self._auto_advance
        return t

    async def sleep(self, delay: float) -> None:
        """Park until advance() moves time past _now + delay."""
        self.sleep_calls.append(delay)
        deadline = self._now + delay
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[None] = loop.create_future()
        self._sleepers.setdefault(deadline, []).append(fut)
        try:
            await fut
        except asyncio.CancelledError:
            # Cancelled externally — clean up without breaking advance()
            bucket = self._sleepers.get(deadline, [])
            try:
                bucket.remove(fut)
            except ValueError:
                pass
            raise

    # ------------------------------------------------------------------
    # Control interface

    def advance(self, dt: float) -> None:
        """Move fake time forward by dt and resolve all due sleepers."""
        self._now += dt
        # Collect deadlines to resolve (sorted so we fire in order)
        due = sorted(d for d in self._sleepers if d <= self._now)
        for deadline in due:
            futures = self._sleepers.pop(deadline, [])
            for fut in futures:
                if not fut.done():
                    fut.set_result(None)

    def next_deadline(self) -> float | None:
        """Return the earliest parked sleeper deadline, or None."""
        if not self._sleepers:
            return None
        return min(self._sleepers)


# ---------------------------------------------------------------------------
# Driving helpers


async def run_ticks(
    fake: FakeTime,
    clock: TickClock,
    n: int,
    *,
    max_yields: int = 10_000,
) -> None:
    """Drive exactly ``n`` tick boundaries through ``clock`` using ``fake``.

    Repeatedly yields to the event loop until a sleeper parks, then advances
    fake time to the next deadline.  Counts completed boundaries via the clock's
    tick counter.  Raises ``TimeoutError`` after ``max_yields`` iterations so
    tests never hang.
    """
    start_tick = clock.tick
    target = start_tick + n
    yields = 0
    while clock.tick < target:
        if yields >= max_yields:
            raise TimeoutError(
                f"run_ticks: did not reach tick {target} after {max_yields} yields "
                f"(stuck at {clock.tick})"
            )
        await asyncio.sleep(0)
        yields += 1
        deadline = fake.next_deadline()
        if deadline is not None:
            # Advance so the sleeper wakes exactly at its deadline
            dt = max(0.0, deadline - fake._now)
            if dt > 0:
                fake.advance(dt)


async def run_until(
    fake: FakeTime,
    predicate: Any,
    *,
    max_yields: int = 10_000,
) -> None:
    """Yield/advance loop until ``predicate()`` is truthy.

    Uses the same yield-then-advance rhythm as ``run_ticks``.
    Raises ``TimeoutError`` after ``max_yields`` iterations.
    """
    yields = 0
    while not predicate():
        if yields >= max_yields:
            raise TimeoutError(f"run_until: predicate still false after {max_yields} yields")
        await asyncio.sleep(0)
        yields += 1
        deadline = fake.next_deadline()
        if deadline is not None:
            dt = max(0.0, deadline - fake._now)
            if dt > 0:
                fake.advance(dt)


def install_fake_time(core: Any, fake: FakeTime) -> TickClock:
    """Rebuild the core's TickClock with ``fake`` as the time source.

    Creates a new TickClock from ``core.config`` with the same parameters,
    substituting ``fake.monotonic`` and ``fake.sleep``.  Reuses the core's
    existing scheduler and event bus (gate is gone in the no-gate redesign).
    Rewires the clock references in the core, event bus, and hook system.

    Must be called BEFORE ``core.start()``.
    """
    from uxok.timing._clock import TickClock

    cfg = core._core_config
    new_clock: TickClock = TickClock(
        tick_rate=cfg.tick_rate,
        scheduler=core._tick_scheduler,
        event_bus=core._event_bus,
        slip_threshold=cfg.tick_slip_threshold,
        precision=cfg.tick_precision,
        busy_wait_us=cfg.tick_busy_wait_us,
        catchup=cfg.tick_catchup,
        time_source=fake.monotonic,
        sleep=fake.sleep,
    )
    core._tick_clock = new_clock
    core._event_bus._clock = new_clock  # type: ignore[attr-defined]
    core._hook_system._clock = new_clock  # type: ignore[attr-defined]
    return new_clock
