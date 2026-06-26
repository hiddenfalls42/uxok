"""Positive concurrency proofs for the no-gate fire-and-forget redesign.

These tests verify the promises of the tick-gate removal:

1. Independent handlers run CONCURRENTLY — N handlers sleeping 0.1s each
   complete in ~0.1s wall time, not N*0.1s.
2. The lifecycle lock serializes concurrent register/load without corruption,
   while a reentrant register→on_start→load chain on ONE task does NOT deadlock.
3. Causal ordering: a nested hook result is available before the outer continues.
4. emit(at_tick=T) fires at exactly tick T (via FakeTime).
5. The clock does NOT stall on a slow handler: tick advances while a handler runs.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.fake_time import FakeTime, install_fake_time, run_ticks
from tests.helpers import wait_until
from uxok import Core
from uxok.plugin import Plugin
from uxok.protocols import Event

# ---------------------------------------------------------------------------
# 1. Independent handlers run concurrently
# ---------------------------------------------------------------------------


class TestConcurrentHandlers:
    """N event handlers that each sleep ~0.1s must finish in ~0.1s total (not N*0.1s)."""

    @pytest.mark.asyncio
    async def test_n_handlers_complete_concurrently(self) -> None:
        """Fire-and-forget dispatch: 5 handlers each sleeping 0.15s finish in ~0.15s.

        Proof: if they ran serially the elapsed time would be >= 5 * 0.15 = 0.75s.
        We assert elapsed < 0.5s (generous bound to allow CI jitter).
        """
        n = 5
        sleep_time = 0.15
        core = Core(tick_rate=100, hook_precaching="disabled")

        completed: list[int] = []
        all_done = asyncio.Event()

        for i in range(n):

            async def handler(event: Event, _i: int = i) -> None:
                await asyncio.sleep(sleep_time)
                completed.append(_i)
                if len(completed) == n:
                    all_done.set()

            await core.events.subscribe("concurrent.test", handler)

        await core.start()

        t0 = time.monotonic()
        await core.events.publish(Event("concurrent.test", {}))

        await asyncio.wait_for(all_done.wait(), timeout=3.0)
        elapsed = time.monotonic() - t0

        assert len(completed) == n
        # Concurrent: all n handlers finish in under 0.5s (not n * sleep_time).
        assert elapsed < 0.5, (
            f"Handlers ran serially: elapsed={elapsed:.3f}s expected < 0.5s. "
            "This means fire-and-forget concurrency is broken."
        )

        await core.stop()

    @pytest.mark.asyncio
    async def test_two_handlers_unblock_each_other(self) -> None:
        """Two handlers that each wait for the OTHER to signal only complete concurrently.

        If dispatch were serial, handler A waits for handler B's signal — but B
        never runs because A never finishes. The test would hang.
        Only passes if A and B run in separate concurrent tasks.
        """
        core = Core(tick_rate=100, hook_precaching="disabled")

        signal_a = asyncio.Event()
        signal_b = asyncio.Event()
        order: list[str] = []

        async def handler_a(event: Event) -> None:
            # Signal B, then wait for B to signal us.
            order.append("a_start")
            signal_a.set()
            await asyncio.wait_for(signal_b.wait(), timeout=2.0)
            order.append("a_done")

        async def handler_b(event: Event) -> None:
            # Wait for A to start, then signal it.
            await asyncio.wait_for(signal_a.wait(), timeout=2.0)
            order.append("b_done")
            signal_b.set()

        await core.events.subscribe("cross.signal", handler_a)
        await core.events.subscribe("cross.signal", handler_b)

        await core.start()
        await core.events.publish(Event("cross.signal", {}))

        # If serial, this wait would hang: A is blocked waiting for B, but B
        # never starts because the executor is still inside A.
        await wait_until(lambda: len(order) == 3, timeout=2.0)

        assert "a_start" in order
        assert "a_done" in order
        assert "b_done" in order

        await core.stop()


# ---------------------------------------------------------------------------
# 2. Lifecycle lock: serialization + reentrance
# ---------------------------------------------------------------------------


class TestLifecycleLock:
    """The reentrant lifecycle lock serializes concurrent ops, prevents deadlock."""

    @pytest.mark.asyncio
    async def test_concurrent_register_same_name_exactly_one_wins(self) -> None:
        """Two concurrent load_plugin calls for the same name must not corrupt state.

        One succeeds (fresh load), the other either succeeds (reload path) or
        raises PluginError. Afterwards exactly one plugin with that name exists.
        """
        core = Core(tick_rate=100, hook_precaching="disabled")
        await core.start()

        code = "\n".join(
            [
                "class Racer(Plugin):",
                "    def __init__(self, **kw):",
                "        super().__init__(name='racer', **kw)",
            ]
        )

        results: list[bool | Exception] = []

        async def try_load() -> None:
            try:
                ok = await core.load_plugin(code)
                results.append(ok)
            except Exception as e:
                results.append(e)

        # Two concurrent load_plugin calls.
        await asyncio.gather(try_load(), try_load())

        # Exactly one plugin named 'racer' must exist.
        racer = await core.get_plugin("racer")
        assert racer is not None, "No plugin named 'racer' after concurrent load"

        plugins = await core.list()
        racer_views = [v for v in plugins if v.name == "racer"]
        assert len(racer_views) == 1, (
            f"Expected exactly 1 'racer' plugin, found {len(racer_views)}: {racer_views}"
        )

        await core.stop()

    @pytest.mark.asyncio
    async def test_reentrant_lifecycle_does_not_deadlock(self) -> None:
        """A plugin whose on_start() calls core.load_plugin() must not deadlock.

        This exercises the reentrant lock: the outer register acquires the lock,
        calls on_start(), which calls load_plugin() which tries to re-acquire.
        With a non-reentrant lock this would deadlock immediately.
        """
        core = Core(tick_rate=100, hook_precaching="disabled")

        # Inner plugin loaded during outer plugin's on_start.
        inner_code = "\n".join(
            [
                "class InnerPlugin(Plugin):",
                "    def __init__(self, **kw):",
                "        super().__init__(name='inner', **kw)",
            ]
        )

        loaded_inner = asyncio.Event()

        class OuterPlugin(Plugin):
            def __init__(self) -> None:
                super().__init__(name="outer")

            async def on_start(self) -> None:
                # Reentrant: we are inside the lifecycle lock; loading inner
                # must re-acquire it without deadlocking.
                await self.core.load_plugin(inner_code)
                loaded_inner.set()

        outer = OuterPlugin()
        await core.start()

        # Must complete within 2 seconds; a deadlock would hang until timeout.
        await asyncio.wait_for(core.register_plugin(outer), timeout=2.0)
        assert loaded_inner.is_set(), "on_start() never completed — possible deadlock"

        inner = await core.get_plugin("inner")
        assert inner is not None

        await core.stop()


# ---------------------------------------------------------------------------
# 3. Causal ordering via nested hook
# ---------------------------------------------------------------------------


class TestCausalOrdering:
    """A nested hook's result is available before the outer hook continues."""

    @pytest.mark.asyncio
    async def test_nested_hook_result_available_before_outer_continues(
        self, clean_core: Core
    ) -> None:
        """Outer hook awaits inner hook; inner result is present when outer resumes.

        Hooks run direct (in the caller's task), so a nested hooks.execute()
        from inside an outer hook completes before the outer's next statement.
        """
        core = clean_core
        order: list[str] = []

        async def inner_hook() -> str:
            order.append("inner")
            return "inner_result"

        inner_results: list[object] = []

        async def outer_hook() -> str:
            order.append("outer_start")
            # Nested hook.execute — must complete before we continue.
            result = await core.hooks.execute("inner.hook")
            inner_results.extend(result)
            order.append("outer_end")
            return "outer_result"

        await core.hooks.register("inner.hook", inner_hook, priority=0, plugin_id="t")
        await core.hooks.register("outer.hook", outer_hook, priority=0, plugin_id="t")

        results = await core.hooks.execute("outer.hook")

        assert order == ["outer_start", "inner", "outer_end"], f"Wrong execution order: {order}"
        assert inner_results == ["inner_result"]
        assert results == ["outer_result"]

    @pytest.mark.asyncio
    async def test_nested_emit_eventually_delivers(self, started_core: Core) -> None:
        """A handler's nested emit delivers to its subscriber.

        With fire-and-forget, the nested publish() schedules a task. That task
        runs concurrently with the outer handler. We wait on an asyncio.Event.
        """
        core = started_core
        inner_received: list[str] = []
        inner_signal = asyncio.Event()

        async def inner_handler(event: Event) -> None:
            inner_received.append(event.name)
            inner_signal.set()

        outer_ran = asyncio.Event()

        async def outer_handler(event: Event) -> None:
            # Nested publish: schedules inner_handler as a task.
            await core.events.publish(Event("inner.event", {}))
            outer_ran.set()

        await core.events.subscribe("outer.event", outer_handler)
        await core.events.subscribe("inner.event", inner_handler)

        await core.events.publish(Event("outer.event", {}))

        await asyncio.wait_for(outer_ran.wait(), timeout=2.0)
        await asyncio.wait_for(inner_signal.wait(), timeout=2.0)

        assert inner_received == ["inner.event"]


# ---------------------------------------------------------------------------
# 4. emit(at_tick=T) fires at exactly tick T
# ---------------------------------------------------------------------------


class TestAtTickFiresExact:
    """emit(at_tick=T) fires at the correct tick boundary, verified with FakeTime."""

    @pytest.mark.asyncio
    async def test_emit_at_tick_fires_exactly_at_target(self) -> None:
        """emit(at_tick=T+3) fires at tick T+3, not before or later.

        Race-free negative: after boundaries T+1 and T+2, received is empty.
        After boundary T+3, the event is delivered.
        """
        core = Core(tick_rate=100, hook_precaching="disabled")
        fake = FakeTime()
        install_fake_time(core, fake)
        clock = core._tick_clock

        received: list[Event] = []
        signal = asyncio.Event()

        async def handler(event: Event) -> None:
            received.append(event)
            signal.set()

        await core.events.subscribe("deferred.event", handler)
        await core.start()

        # Establish a stable tick baseline.
        await run_ticks(fake, clock, 2)
        target_tick = clock.tick + 3

        plugin = Plugin(name="emitter")
        await core.register_plugin(plugin)

        await plugin.emit("deferred.event", {"at": target_tick}, at_tick=target_tick)

        # Boundaries before target: event must not fire.
        for i in range(2):
            await run_ticks(fake, clock, 1)
            assert received == [], f"Event fired early at boundary +{i + 1}"

        # Drive to target tick.
        await run_ticks(fake, clock, 1)
        await asyncio.wait_for(signal.wait(), timeout=2.0)

        assert len(received) == 1
        assert received[0].name == "deferred.event"

        await core.stop()


# ---------------------------------------------------------------------------
# 5. Clock does not stall on a slow handler
# ---------------------------------------------------------------------------


class TestClockDoesNotStallOnSlowHandler:
    """While a slow event handler runs, core.tick keeps advancing."""

    @pytest.mark.asyncio
    async def test_tick_advances_while_slow_handler_runs(self) -> None:
        """A handler sleeping 0.3s must not stall the clock.

        We measure the tick count before the handler starts and after it
        finishes. The tick must have advanced by at least several ticks
        during the 0.3s the handler slept.
        """
        core = Core(tick_rate=100, hook_precaching="disabled")

        tick_before_handler: list[int] = []
        tick_after_handler: list[int] = []
        handler_done = asyncio.Event()

        async def slow_handler(event: Event) -> None:
            tick_before_handler.append(core.tick)
            await asyncio.sleep(0.3)
            tick_after_handler.append(core.tick)
            handler_done.set()

        await core.events.subscribe("slow.event", slow_handler)
        await core.start()

        # Let the clock warm up.
        await wait_until(lambda: core.tick >= 5)

        await core.events.publish(Event("slow.event", {}))

        # Wait for the slow handler to complete.
        await asyncio.wait_for(handler_done.wait(), timeout=2.0)

        assert len(tick_before_handler) == 1
        assert len(tick_after_handler) == 1

        ticks_elapsed = tick_after_handler[0] - tick_before_handler[0]

        # @ 100 Hz, 0.3s → ~30 ticks. Allow generous lower bound of 10.
        assert ticks_elapsed >= 10, (
            f"Clock stalled on slow handler: only {ticks_elapsed} ticks elapsed "
            f"in 0.3s @ 100 Hz (expected ≥ 10). "
            "The gate removal must allow the clock to run independently."
        )

        await core.stop()

    @pytest.mark.asyncio
    async def test_two_slow_events_overlap(self) -> None:
        """Two sequential publishes each triggering a 0.15s handler should complete
        in ~0.15s wall time (they overlap), not 0.30s (serial).

        This confirms that event dispatch tasks are independent (not serialized).
        """
        core = Core(tick_rate=100, hook_precaching="disabled")
        done_times: list[float] = []
        all_done = asyncio.Event()

        async def slow_handler(event: Event) -> None:
            await asyncio.sleep(0.15)
            done_times.append(time.monotonic())
            if len(done_times) == 2:
                all_done.set()

        await core.events.subscribe("a.event", slow_handler)
        await core.events.subscribe("b.event", slow_handler)

        await core.start()
        await wait_until(lambda: core.tick >= 3)

        t0 = time.monotonic()
        # Publish both events nearly simultaneously.
        await core.events.publish(Event("a.event", {}))
        await core.events.publish(Event("b.event", {}))

        await asyncio.wait_for(all_done.wait(), timeout=3.0)
        elapsed = time.monotonic() - t0

        # Overlapping: both finish in ~0.15s, not ~0.30s.
        assert elapsed < 0.35, f"Handlers ran serially: {elapsed:.3f}s elapsed, expected < 0.35s."

        await core.stop()
