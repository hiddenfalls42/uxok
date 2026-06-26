"""Tick-locked scheduling for deferred operations."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ScheduledEntry:
    """A scheduled coroutine factory with an optional owning plugin instance.

    Ownership is by instance identity (not plugin ID): during hot reload the
    old and new instances share an ID, and draining the old instance must not
    cancel schedules the new instance registered in its on_start().
    """

    factory: Callable[[], Coroutine[Any, Any, Any]]
    owner: object | None = None


class TickScheduler:
    """Manages at_tick deferred operations.

    Scheduled operations are checked by the tick clock on each boundary.
    Due work is launched as fire-and-forget asyncio tasks so the clock loop
    is never blocked on the result.
    """

    def __init__(self) -> None:
        # {tick_number: [entry, ...]}
        self._at_tick: dict[int, list[_ScheduledEntry]] = defaultdict(list)

        # Last tick whose due work was collected. Due-collection is
        # range-based — (last, current] — so skip-mode tick jumps fire
        # everything due in the gap exactly once instead of stranding it.
        self._last_processed_tick: int = 0

        # Launched tasks tracked for cancellation on shutdown.
        self._tasks: set[asyncio.Task[Any]] = set()

    def schedule_at(
        self,
        tick: int,
        current_tick: int,
        factory: Callable[[], Coroutine[Any, Any, Any]],
        owner: object | None = None,
    ) -> None:
        """Schedule a coroutine factory to fire at a specific tick.

        Args:
            tick: Target tick number. Must be > current_tick.
            current_tick: Current tick (for validation).
            factory: Zero-argument callable returning a coroutine.
            owner: Owning plugin instance; its schedules are removed when the
                   plugin's resources are drained.

        Raises:
            ValueError: If tick <= current_tick (scheduling in the past).
        """
        if tick <= current_tick:
            raise ValueError(
                f"Cannot schedule at tick {tick}: current tick is {current_tick}. "
                "Scheduling in the past is not allowed."
            )
        self._at_tick[tick].append(_ScheduledEntry(factory, owner))

    def unschedule_owner(self, owner: object) -> int:
        """Remove all schedules belonging to an owner (plugin instance).

        Called when a plugin's resources are drained, so unregistered or
        hot-swapped plugins leave no zombie deferred work behind.

        Returns:
            Number of entries removed.
        """
        removed = 0
        for key in list(self._at_tick.keys()):
            entries = self._at_tick[key]
            kept = [e for e in entries if e.owner is not owner]
            removed += len(entries) - len(kept)
            if kept:
                self._at_tick[key] = kept
            else:
                del self._at_tick[key]
        if removed:
            logger.debug("Unscheduled %d entries for owner %r", removed, owner)
        return removed

    async def tick(self, current_tick: int) -> None:
        """Called by TickClock on each boundary. Fires work due in
        (last_processed, current_tick].

        With burst catch-up the range is always a single tick (legacy
        behavior). With skip catch-up the clock may jump several ticks at
        once: at_tick entries in the gap fire once (late).
        """
        last = self._last_processed_tick
        if current_tick <= last:
            return
        self._last_processed_tick = current_tick

        # Fire at_tick entries due in the range, in tick order
        due_ticks = sorted(t for t in self._at_tick if last < t <= current_tick)
        for t in due_ticks:
            for entry in self._at_tick.pop(t, []):
                self._launch_entry(entry)

    def _launch_entry(self, entry: _ScheduledEntry) -> None:
        """Build and launch one scheduled coroutine as a tracked task.

        Factory failures are isolated here — an exception must not propagate
        to the tick loop's task (which would kill the clock).
        """
        try:
            coro = entry.factory()
        except Exception:
            logger.exception("Scheduled factory raised; firing skipped (owner=%r)", entry.owner)
            return
        task = asyncio.create_task(coro, name="uxok.scheduled_work")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def cancel_all(self) -> None:
        """Cancel all in-flight scheduled tasks. Called by Core.stop()."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
