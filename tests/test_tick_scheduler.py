"""Tests for TickScheduler — no-gate, task-launching scheduler.

TickScheduler.tick() fires due work as asyncio tasks (fire-and-forget), so
tests must await the tasks to observe their side-effects.  The scheduler
exposes _tasks for inspection; we gather any in-flight tasks after each tick.
"""

from __future__ import annotations

import asyncio

import pytest

from uxok.timing._scheduler import TickScheduler

# ---------------------------------------------------------------------------
# Helper: drain all in-flight scheduler tasks
# ---------------------------------------------------------------------------


async def _drain(scheduler: TickScheduler, *, timeout: float = 1.0) -> None:
    """Await all in-flight tasks tracked by the scheduler."""
    tasks = list(scheduler._tasks)
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)


# ---------------------------------------------------------------------------
# TestScheduleAt
# ---------------------------------------------------------------------------


class TestScheduleAt:
    """schedule_at / at_tick basic contract."""

    @pytest.mark.asyncio
    async def test_schedule_at_future_tick(self):
        """schedule_at(10) fires when tick(10) is called and not before."""
        scheduler = TickScheduler()
        executed: list[str] = []

        async def operation() -> None:
            executed.append("fired")

        scheduler.schedule_at(10, current_tick=0, factory=lambda: operation())

        for t in range(1, 10):
            await scheduler.tick(t)
            await _drain(scheduler)
            assert "fired" not in executed

        await scheduler.tick(10)
        await _drain(scheduler)

        assert executed == ["fired"]

    @pytest.mark.asyncio
    async def test_schedule_at_removes_after_firing(self):
        """Scheduled operation is one-shot: does not fire again after tick(10)."""
        scheduler = TickScheduler()
        executed: list[str] = []

        async def operation() -> None:
            executed.append("fired")

        scheduler.schedule_at(5, current_tick=0, factory=lambda: operation())
        await scheduler.tick(5)
        await _drain(scheduler)
        assert executed == ["fired"]

        executed.clear()
        await scheduler.tick(10)
        await _drain(scheduler)
        assert executed == []

    @pytest.mark.asyncio
    async def test_schedule_at_past_tick_raises(self):
        """Scheduling in the past raises ValueError."""
        scheduler = TickScheduler()

        async def operation() -> None:
            pass

        with pytest.raises(ValueError, match="Cannot schedule at tick"):
            scheduler.schedule_at(5, current_tick=10, factory=lambda: operation())

    @pytest.mark.asyncio
    async def test_schedule_at_current_tick_raises(self):
        """Scheduling at the current tick raises ValueError (must be strictly future)."""
        scheduler = TickScheduler()

        async def operation() -> None:
            pass

        with pytest.raises(ValueError, match="Cannot schedule at tick"):
            scheduler.schedule_at(5, current_tick=5, factory=lambda: operation())

    @pytest.mark.asyncio
    async def test_multiple_at_same_tick(self):
        """Multiple operations scheduled for the same tick all fire."""
        scheduler = TickScheduler()
        executed: list[int] = []

        async def op(n: int) -> None:
            executed.append(n)

        scheduler.schedule_at(3, current_tick=0, factory=lambda: op(1))
        scheduler.schedule_at(3, current_tick=0, factory=lambda: op(2))

        await scheduler.tick(3)
        await _drain(scheduler)

        assert sorted(executed) == [1, 2]


# ---------------------------------------------------------------------------
# TestSchedulerOwnership
# ---------------------------------------------------------------------------


class TestSchedulerOwnership:
    """unschedule_owner removes entries by instance identity."""

    @pytest.mark.asyncio
    async def test_at_tick_removed_on_unregister(self, clean_core):
        """A plugin's deferred emit must not survive unregistration."""
        from uxok import Plugin

        class Deferred(Plugin):
            def __init__(self):
                super().__init__(name="deferred")

        core = clean_core
        plugin = Deferred()
        await core.register_plugin(plugin)

        await plugin.emit("later", {}, at_tick=core.tick + 10_000)
        scheduler = core._tick_scheduler
        assert sum(len(v) for v in scheduler._at_tick.values()) == 1

        await core.unregister_plugin("deferred")

        assert scheduler._at_tick == {}

    @pytest.mark.asyncio
    async def test_unschedule_owner_returns_removed_count(self):
        """unschedule_owner removes owned entries and returns count."""
        scheduler = TickScheduler()
        owner = object()

        async def op() -> None:
            pass

        scheduler.schedule_at(10, current_tick=0, factory=lambda: op(), owner=owner)
        scheduler.schedule_at(20, current_tick=0, factory=lambda: op(), owner=owner)
        scheduler.schedule_at(30, current_tick=0, factory=lambda: op())  # unowned

        assert scheduler.unschedule_owner(owner) == 2
        assert sum(len(v) for v in scheduler._at_tick.values()) == 1


# ---------------------------------------------------------------------------
# TestRangeBasedDueCollection
# ---------------------------------------------------------------------------


class TestRangeBasedDueCollection:
    """Skip-mode tick jumps must not strand scheduled work."""

    @pytest.mark.asyncio
    async def test_at_tick_in_skipped_range_fires_once_late(self):
        """schedule_at(3) fires when clock jumps from 1 to 7 (range-based collection)."""
        scheduler = TickScheduler()
        fired: list[str] = []

        async def op() -> None:
            fired.append("at3")

        scheduler.schedule_at(3, current_tick=1, factory=lambda: op())

        await scheduler.tick(1)
        await _drain(scheduler)
        assert fired == []

        await scheduler.tick(7)
        await _drain(scheduler)

        assert fired == ["at3"]
        assert scheduler._at_tick == {}

    @pytest.mark.asyncio
    async def test_stale_or_duplicate_tick_is_ignored(self):
        """schedule_at(5) fires exactly once even with duplicate or stale ticks."""
        scheduler = TickScheduler()
        count = 0

        async def op() -> None:
            nonlocal count
            count += 1

        scheduler.schedule_at(5, current_tick=0, factory=lambda: op())
        await scheduler.tick(5)
        await _drain(scheduler)
        await scheduler.tick(5)  # duplicate
        await _drain(scheduler)
        await scheduler.tick(3)  # stale
        await _drain(scheduler)

        assert count == 1


# ---------------------------------------------------------------------------
# TestSchedulerRobustness
# ---------------------------------------------------------------------------


class TestSchedulerRobustness:
    """Factory exceptions must not propagate out of tick()."""

    @pytest.mark.asyncio
    async def test_factory_exception_is_isolated(self):
        """A synchronously-raising factory does not kill tick(); healthy entries fire."""
        scheduler = TickScheduler()
        ran: list[str] = []

        def bad_factory():
            raise RuntimeError("user factory bug")

        async def good() -> None:
            ran.append("good")

        scheduler.schedule_at(1, current_tick=0, factory=bad_factory)
        scheduler.schedule_at(1, current_tick=0, factory=lambda: good())

        await scheduler.tick(1)  # must not raise
        await _drain(scheduler)

        assert ran == ["good"]

    @pytest.mark.asyncio
    async def test_cancel_all_cancels_in_flight_tasks(self):
        """cancel_all() cancels in-flight tasks without raising."""
        scheduler = TickScheduler()
        started = asyncio.Event()

        async def long_task() -> None:
            started.set()
            await asyncio.sleep(100)

        scheduler.schedule_at(1, current_tick=0, factory=lambda: long_task())
        await scheduler.tick(1)

        # Wait for the task to start.
        await asyncio.wait_for(started.wait(), timeout=1.0)

        await scheduler.cancel_all()
        assert len(scheduler._tasks) == 0

    @pytest.mark.asyncio
    async def test_due_work_launched_as_tasks_not_blocking(self):
        """tick() returns immediately; the work runs concurrently as a task."""
        scheduler = TickScheduler()
        completed = asyncio.Event()

        async def slow_work() -> None:
            await asyncio.sleep(0.01)
            completed.set()

        scheduler.schedule_at(1, current_tick=0, factory=lambda: slow_work())

        # tick() must return without awaiting the work.
        await scheduler.tick(1)
        assert not completed.is_set(), "tick() must not await the scheduled work"

        # Work completes asynchronously.
        await asyncio.wait_for(completed.wait(), timeout=2.0)
        assert completed.is_set()
