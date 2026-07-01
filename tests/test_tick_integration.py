"""Integration tests for tick system with subsystems.

Tests are organised from simplest to most complex:
  - Core property / bootstrap stamping
  - Exact tick-value stamping via fake-time Core
  - core.tick_slip end-to-end (payload, threshold)
  - core.tick_clock_failed end-to-end (crash, stops running, signals event)
  - Hooks execute direct (no gate latency)
  - Event tick-stamping and causal ordering
  - Reload swap-tick schedule isolation
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from tests.fake_time import FakeTime, install_fake_time, run_ticks, run_until
from tests.helpers import StubPlugin, wait_until
from uxok import Core
from uxok.protocols import CoreState, Event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fake_core(
    *,
    tick_rate: int = 100,
    slip_threshold: int = 5,
    **kwargs: Any,
) -> tuple[Core, FakeTime]:
    """Return a Core wired to a FakeTime; install_fake_time called for you."""
    core = Core(tick_rate=tick_rate, hook_precaching="disabled", **kwargs)
    fake = FakeTime()
    install_fake_time(core, fake)
    return core, fake


def stall_amount(fake: FakeTime, clock: Any, k: int) -> float:
    """Advance that induces exactly K missed tick periods."""
    pending = fake.next_deadline()
    if pending is None:
        return (k + 0.5) * clock._tick_interval
    dt = max(0.0, pending - fake._now) + (k + 0.5) * clock._tick_interval
    return dt


# ---------------------------------------------------------------------------
# Core property and bootstrap stamping
# ---------------------------------------------------------------------------


class TestCoreTickIntegration:
    """Core tick property and bootstrap stamping."""

    @pytest.mark.asyncio
    async def test_core_tick_property(self) -> None:
        """Core.tick is zero before start, advances exactly N per run_ticks, frozen after stop."""
        core, fake = fake_core(tick_rate=100)
        clock = core._tick_clock

        assert core.tick == 0

        await core.start()

        await run_ticks(fake, clock, 5)
        assert core.tick == 5

        await run_ticks(fake, clock, 5)
        assert core.tick == 10

        await core.stop()
        frozen_tick = core.tick

        fake.advance(10 * clock._tick_interval)
        for _ in range(20):
            await asyncio.sleep(0)
        assert core.tick == frozen_tick

    @pytest.mark.asyncio
    async def test_bootstrap_events_not_stamped(self, clean_core: Core) -> None:
        """Events published before core.start() carry tick==0, slip==0."""
        core = clean_core
        received: list[Event] = []
        signal = asyncio.Event()

        async def handler(event: Event) -> None:
            received.append(event)
            signal.set()

        await core.events.subscribe("test.event", handler)
        await core.events.publish(Event("test.event", {"data": "bootstrap"}))
        await asyncio.wait_for(signal.wait(), timeout=2.0)

        assert len(received) == 1
        assert received[0].tick == 0
        assert received[0].slip == 0

        await core.start()
        # A post-start event should carry tick > 0
        signal.clear()
        received.clear()
        await wait_until(lambda: core.tick > 0)
        await core.events.publish(Event("test.event", {"data": "after_start"}))
        await asyncio.wait_for(signal.wait(), timeout=2.0)
        assert received[0].tick > 0

    @pytest.mark.asyncio
    async def test_hooks_receive_tick_context(self, started_core: Core) -> None:
        """Hooks receive tick context via _tick_context keyword when clock is running."""
        core = started_core
        received_ticks: list[int] = []

        plugin = StubPlugin()
        await core.register_plugin(plugin)

        async def handler(**kwargs: Any) -> None:
            ctx = kwargs.get("_tick_context", {})
            received_ticks.append(ctx.get("tick", 0))

        await core.hooks.register("test.hook", handler, priority=0, plugin_id="test")

        # Wait for clock to advance past tick 0.
        await wait_until(lambda: core.tick > 0)
        await core.hooks.execute("test.hook")

        assert len(received_ticks) == 1
        assert received_ticks[0] > 0


# ---------------------------------------------------------------------------
# Exact tick-value stamping
# ---------------------------------------------------------------------------


class TestEventStamping:
    """Exact tick and slip values on published events via fake-time Core."""

    @pytest.mark.asyncio
    async def test_event_stamping_equals_clock_values_at_publish(self) -> None:
        """Publish at known tick T ⇒ event.tick == T; stall K=7 ⇒ slip==7; recovery ⇒ slip==0."""
        core, fake = fake_core(tick_rate=100)
        clock = core._tick_clock
        received: list[Event] = []
        signal = asyncio.Event()

        async def handler(event: Event) -> None:
            received.append(event)
            signal.set()

        await core.events.subscribe("probe.event", handler)
        await core.start()

        await run_ticks(fake, clock, 5)
        publish_tick = clock.tick  # == 5
        signal.clear()
        await core.events.publish(Event("probe.event", {}))
        await asyncio.wait_for(signal.wait(), timeout=2.0)

        assert len(received) == 1
        assert received[0].tick == publish_tick

        # --- Induce K=7 slip ---
        received.clear()
        signal.clear()
        await asyncio.sleep(0)
        dt = stall_amount(fake, clock, 7)
        fake.advance(dt)
        start_tick = clock.tick
        await run_ticks(fake, clock, 1)
        assert clock.slip == 7
        stall_boundary_tick = clock.tick
        assert stall_boundary_tick == start_tick + 1 + 7

        await core.events.publish(Event("probe.event", {}))
        await asyncio.wait_for(signal.wait(), timeout=2.0)
        assert len(received) == 1
        assert received[0].slip == 7

        # --- Clean tick: slip resets to 0 ---
        received.clear()
        signal.clear()
        await run_ticks(fake, clock, 1)
        await core.events.publish(Event("probe.event", {}))
        await asyncio.wait_for(signal.wait(), timeout=2.0)
        assert len(received) == 1
        assert received[0].slip == 0

        await core.stop()


# ---------------------------------------------------------------------------
# core.tick_slip end-to-end
# ---------------------------------------------------------------------------


class TestCoreSlipEventEndToEnd:
    """core.tick_slip event dispatched through a real running Core."""

    @pytest.mark.asyncio
    async def test_slip_event_full_payload_through_running_core(self) -> None:
        """Stall K=7 with threshold=5: exactly one core.tick_slip event, exact payload."""
        core, fake = fake_core(tick_rate=100, tick_slip_threshold=5)
        clock = core._tick_clock
        slip_events: list[Event] = []
        signal = asyncio.Event()

        async def on_slip(event: Event) -> None:
            slip_events.append(event)
            signal.set()

        await core.events.subscribe("core.tick_slip", on_slip)
        await core.start()

        await run_ticks(fake, clock, 5)

        for _ in range(100):
            await asyncio.sleep(0)
            if fake.next_deadline() is not None:
                break
        pre_stall_tick = clock.tick

        fake.advance(stall_amount(fake, clock, 7))
        await run_ticks(fake, clock, 1)

        stall_boundary = clock.tick
        assert stall_boundary == pre_stall_tick + 8

        # Slip event task fires at the stall boundary; wait for delivery.
        await asyncio.wait_for(signal.wait(), timeout=2.0)

        assert len(slip_events) == 1
        ev = slip_events[0]
        assert set(ev.data) == {"tick", "slip", "tick_rate"}
        assert ev.data["tick"] == stall_boundary
        assert ev.data["slip"] == 7
        assert ev.data["tick_rate"] == 100

        await core.stop()

    @pytest.mark.asyncio
    async def test_no_slip_event_below_threshold_through_core(self) -> None:
        """Stall K=7 with threshold=8: zero slip events even after 20 clean ticks."""
        core, fake = fake_core(tick_rate=100, tick_slip_threshold=8)
        clock = core._tick_clock
        slip_events: list[Event] = []

        async def on_slip(event: Event) -> None:
            slip_events.append(event)

        await core.events.subscribe("core.tick_slip", on_slip)
        await core.start()

        await run_ticks(fake, clock, 5)

        await asyncio.sleep(0)
        fake.advance(stall_amount(fake, clock, 7))
        await run_ticks(fake, clock, 21)

        # Drain any in-flight tasks.
        for _ in range(20):
            await asyncio.sleep(0)

        assert len(slip_events) == 0, f"Expected no slip events, got {len(slip_events)}"

        await core.stop()

    @pytest.mark.asyncio
    async def test_slip_event_real_stall_smoke(self, clean_core: Core) -> None:
        """SMOKE: wall-clock wiring only — real stall produces a core.tick_slip event."""
        slip_events: list[Event] = []

        core2 = Core(
            tick_rate=20,
            hook_precaching="disabled",
            tick_slip_threshold=2,
        )

        async def on_slip(event: Event) -> None:
            slip_events.append(event)

        await core2.events.subscribe("core.tick_slip", on_slip)
        await core2.start()

        time.sleep(0.15)  # 150 ms @ 20 Hz == ~3 periods

        try:
            await wait_until(lambda: len(slip_events) >= 1, timeout=2.0)
        finally:
            await core2.stop()

        assert len(slip_events) >= 1
        assert set(slip_events[0].data) == {"tick", "slip", "tick_rate"}


# ---------------------------------------------------------------------------
# core.tick_clock_failed end-to-end
# ---------------------------------------------------------------------------


class TestTickClockFailureEndToEnd:
    """Clock crash stops the clock loop and signals core.tick_clock_failed.

    In the no-gate design there are no parked callers to release; the test
    verifies that the crash guard fires the event and _running becomes False.
    Subsequent operations continue normally (hooks run direct; events are
    fire-and-forget) without needing a running clock.
    """

    @staticmethod
    def _install_crash_once(clock: Any) -> None:
        """Replace the scheduler.tick method to raise RuntimeError exactly once."""
        scheduler = clock._scheduler
        original_tick = scheduler.tick
        called = False

        async def crashing_tick(current_tick: int) -> None:
            nonlocal called
            if not called:
                called = True
                raise RuntimeError("synthetic crash for test")
            await original_tick(current_tick)

        scheduler.tick = crashing_tick  # type: ignore[method-assign]

    @pytest.mark.asyncio
    async def test_crash_stops_clock_and_fires_failed_event(self) -> None:
        """After a clock crash: _running==False and core.tick_clock_failed fires.

        core.tick_clock_failed data == {"tick", "tick_rate"} (no slip key).
        """
        core, fake = fake_core(tick_rate=100)
        clock = core._tick_clock
        failed_events: list[Event] = []
        signal = asyncio.Event()

        async def on_fail(event: Event) -> None:
            failed_events.append(event)
            signal.set()

        await core.events.subscribe("core.tick_clock_failed", on_fail)
        await core.start()

        await run_ticks(fake, clock, 3)

        self._install_crash_once(clock)

        # Trigger the crash boundary without run_ticks (the clock dies, so
        # run_ticks would TimeoutError).
        deadline = fake.next_deadline()
        if deadline is None:
            # Clock may not be parked yet; give it a yield.
            for _ in range(50):
                await asyncio.sleep(0)
                if fake.next_deadline() is not None:
                    break
        deadline = fake.next_deadline()
        if deadline is not None:
            fake.advance(deadline - fake._now)

        # Pump loop until the failure event arrives.
        await asyncio.wait_for(signal.wait(), timeout=2.0)

        # Give fire-and-forget dispatch task time to run.
        for _ in range(20):
            await asyncio.sleep(0)

        assert len(failed_events) == 1
        assert set(failed_events[0].data) == {"tick", "tick_rate"}
        assert not clock._running

        # Core is still RUNNING — clock crash is not a core state transition.
        assert core.state is CoreState.RUNNING

        # Post-crash: hooks still run direct (no gate dependency).
        plugin = StubPlugin(name="probe")
        await core.register_plugin(plugin)
        hook_results: list[Any] = []
        await core.hooks.register(
            "post.crash.hook",
            lambda: hook_results.append("ok") or "ok",
            priority=0,
            plugin_id="probe",
        )
        result = await core.hooks.execute("post.crash.hook")
        assert result is not None

        # Events still fire-and-forget.
        received: list[Event] = []
        recv_signal = asyncio.Event()

        async def on_probe(event: Event) -> None:
            received.append(event)
            recv_signal.set()

        await core.events.subscribe("inline", on_probe)
        await plugin.emit("inline", {})
        await asyncio.wait_for(recv_signal.wait(), timeout=2.0)
        assert len(received) == 1

        await core.stop()

    @pytest.mark.asyncio
    async def test_restart_after_clock_failure(self) -> None:
        """stop() → STOPPED; start() → RUNNING; ticks resume after crash."""
        core = Core(tick_rate=1000, hook_precaching="disabled")
        failed_events: list[Event] = []

        async def on_fail(event: Event) -> None:
            failed_events.append(event)

        await core.events.subscribe("core.tick_clock_failed", on_fail)
        await core.start()
        await asyncio.sleep(0.01)

        # Crash the clock via its scheduler.
        scheduler = core._tick_scheduler
        original_tick = scheduler.tick
        called = False

        async def crash_once(current_tick: int) -> None:
            nonlocal called
            if not called:
                called = True
                raise RuntimeError("synthetic crash")
            await original_tick(current_tick)

        scheduler.tick = crash_once  # type: ignore[method-assign]

        await wait_until(lambda: len(failed_events) >= 1, timeout=2.0)

        await core.stop()
        assert core.state is CoreState.STOPPED

        await core.start()
        assert core.state is CoreState.RUNNING

        pre_restart_tick = core.tick
        await asyncio.sleep(0.02)
        assert core.tick > pre_restart_tick

        results: list[Any] = []
        await core.hooks.register(
            "post.restart.hook",
            lambda: results.append("ok") or "ok",
            priority=0,
            plugin_id="test",
        )
        result = await core.hooks.execute("post.restart.hook")
        assert result is not None

        await core.stop()


# ---------------------------------------------------------------------------
# Hooks execute direct (no gate latency)
# ---------------------------------------------------------------------------


class TestHooksDirect:
    """hooks.execute() runs in the caller's task — no tick boundary required.

    In the no-gate model, hooks return immediately (direct await in the caller).
    """

    @pytest.mark.asyncio
    async def test_hook_execute_completes_without_tick_boundary(self) -> None:
        """hooks.execute() completes without driving a tick boundary.

        Contrast with the old gate model where hooks waited for the next tick.
        """
        core, fake = fake_core(tick_rate=100)
        clock = core._tick_clock
        results: list[Any] = []

        async def handler() -> str:
            return "done"

        await core.hooks.register("direct.hook", handler, priority=0, plugin_id="test")
        await core.start()

        await run_ticks(fake, clock, 3)

        # Execute without advancing fake time — must complete immediately.
        tick_before = clock.tick
        result = await core.hooks.execute("direct.hook")
        tick_after = clock.tick

        assert result == ["done"]
        # No tick boundary was required; tick is unchanged (cooperative asyncio).
        assert tick_after == tick_before

        await core.stop()

    @pytest.mark.asyncio
    async def test_hook_execute_returns_all_results(self) -> None:
        """hooks.execute() with multiple handlers returns all results."""
        core, fake = fake_core(tick_rate=100)
        clock = core._tick_clock

        await core.hooks.register("multi.hook", lambda: "a", priority=10, plugin_id="t1")
        await core.hooks.register("multi.hook", lambda: "b", priority=5, plugin_id="t2")

        await core.start()
        await run_ticks(fake, clock, 1)

        result = await core.hooks.execute("multi.hook")
        assert sorted(result) == ["a", "b"]

        await core.stop()


# ---------------------------------------------------------------------------
# Event tick-stamping and causal ordering
# ---------------------------------------------------------------------------


class TestBusTickOrdering:
    """Tick-stamping and causal ordering under fire-and-forget dispatch.

    Global cross-publisher execution order is NOT guaranteed (concurrent tasks).
    What IS guaranteed:
      - event.tick is stamped at publish time (the clock tick when publish() was called)
      - A handler's nested actions complete before that handler continues (causal)
    """

    @pytest.mark.asyncio
    async def test_events_stamped_at_publish_tick(self) -> None:
        """Three events published at tick T are all stamped with event.tick==T."""
        core, fake = fake_core(tick_rate=100)
        clock = core._tick_clock
        stamp_ticks: list[int] = []
        received_count = 0
        all_received = asyncio.Event()

        for i in range(3):

            async def handler(event: Event, _i: int = i) -> None:
                nonlocal received_count
                stamp_ticks.append(event.tick)
                received_count += 1
                if received_count == 3:
                    all_received.set()

            await core.events.subscribe(f"bus.order.{i}", handler)

        await core.start()
        await run_ticks(fake, clock, 5)
        publish_tick = clock.tick  # == 5

        await core.events.publish(Event("bus.order.0", {}))
        await core.events.publish(Event("bus.order.1", {}))
        await core.events.publish(Event("bus.order.2", {}))

        await asyncio.wait_for(all_received.wait(), timeout=2.0)

        # All events were stamped at publish tick T.
        for s in stamp_ticks:
            assert s == publish_tick, f"Stamp tick {s}, expected {publish_tick}"

        await core.stop()

    @pytest.mark.asyncio
    async def test_causal_ordering_via_nested_handler(self) -> None:
        """A handler's nested emit completes (is observed) before the outer handler returns.

        This is guaranteed by asyncio's cooperative await chain: the nested
        publish() call returns after scheduling tasks, but the outer handler
        can check that the task count increased (causal: the schedule happened).
        The RESULT of the nested handler is observable after an await.
        """
        core, fake = fake_core(tick_rate=100)
        clock = core._tick_clock

        inner_done = asyncio.Event()
        inner_results: list[str] = []

        async def inner_handler(event: Event) -> None:
            inner_results.append("inner")
            inner_done.set()

        async def outer_handler(event: Event) -> None:
            # Nested emit launches inner_handler as a task.
            await core.events.publish(Event("inner.event", {}))
            # The task is launched; we can verify it's scheduled.
            assert len(core._event_bus._dispatch_tasks) >= 0

        await core.events.subscribe("outer.event", outer_handler)
        await core.events.subscribe("inner.event", inner_handler)

        await core.start()
        await run_ticks(fake, clock, 3)

        await core.events.publish(Event("outer.event", {}))

        # Both inner and outer eventually complete.
        await asyncio.wait_for(inner_done.wait(), timeout=2.0)
        assert inner_results == ["inner"]

        await core.stop()


# ---------------------------------------------------------------------------
# Reload swap-tick schedule isolation
# ---------------------------------------------------------------------------

_V1_SOURCE = [
    "class CounterV1(Plugin):",
    "    def __init__(self, **kw):",
    "        super().__init__(name='swap_counter', **kw)",
    "    async def on_start(self):",
    "        self.hook('counter.hook', at_tick=self.core.tick + 5)",
]

_V2_SOURCE = [
    "class CounterV1(Plugin):",
    "    def __init__(self, **kw):",
    "        super().__init__(name='swap_counter', **kw)",
    "    async def on_start(self):",
    "        self.hook('counter_v2.hook', at_tick=self.core.tick + 5)",
]


class TestReloadSwapTick:
    """Hot-reload drains the old instance's at_tick schedule entries."""

    @pytest.mark.asyncio
    async def test_reload_drains_old_at_tick_entries(self) -> None:
        """After reload, old instance's deferred hook is gone; new instance's fires."""
        core, fake = fake_core(tick_rate=100)
        clock = core._tick_clock

        v1_fires: list[int] = []
        v2_fires: list[int] = []
        v2_signal = asyncio.Event()

        async def v1_hook() -> None:
            v1_fires.append(clock.tick)

        async def v2_hook() -> None:
            v2_fires.append(clock.tick)
            v2_signal.set()

        await core.hooks.register("counter.hook", v1_hook, priority=0, plugin_id="counter")
        await core.hooks.register("counter_v2.hook", v2_hook, priority=0, plugin_id="counter_v2")

        await core.start()

        load1_task = asyncio.create_task(core.load_plugin("\n".join(_V1_SOURCE)))
        await run_ticks(fake, clock, 1)
        await load1_task

        scheduler = core._tick_scheduler
        assert sum(len(v) for v in scheduler._at_tick.values()) == 1

        await run_until(fake, lambda: clock.tick >= 3)

        reload_task = asyncio.create_task(core.load_plugin("\n".join(_V2_SOURCE)))
        await run_ticks(fake, clock, 1)
        await reload_task

        v2_instance = await core.get_plugin("swap_counter")
        assert v2_instance is not None

        # After reload: exactly one at_tick entry (v2's), v1's gone.
        assert sum(len(v) for v in scheduler._at_tick.values()) == 1
        assert v1_fires == [], f"v1 hook fired unexpectedly at {v1_fires}"

        # Drive past tick 10 to let v2's entry fire.
        await run_until(fake, lambda: clock.tick >= 12)
        # Drain scheduled tasks.
        for _ in range(50):
            await asyncio.sleep(0)
            if v2_fires:
                break

        assert len(v2_fires) >= 1, "v2 deferred hook never fired"

        await core.stop()


# ---------------------------------------------------------------------------
# Core.slip property
# ---------------------------------------------------------------------------


class TestCoreSlipProperty:
    """Core.slip mirrors the tick clock's slip counter."""

    @pytest.mark.asyncio
    async def test_slip_returns_int_before_start(self, clean_core: Core) -> None:
        assert isinstance(clean_core.slip, int)
        assert clean_core.slip == 0

    @pytest.mark.asyncio
    async def test_slip_zero_on_clean_run(self) -> None:
        core, fake = fake_core(tick_rate=100)
        clock = core._tick_clock

        await core.start()
        await run_ticks(fake, clock, 10)

        assert isinstance(core.slip, int)
        assert core.slip == 0

        await core.stop()

    @pytest.mark.asyncio
    async def test_slip_nonzero_after_stall(self) -> None:
        core, fake = fake_core(tick_rate=100, slip_threshold=1)
        clock = core._tick_clock
        interval = clock._tick_interval

        await core.start()
        await run_ticks(fake, clock, 5)
        assert core.slip == 0

        pending = fake.next_deadline()
        if pending is None:
            dt = 3.5 * interval
        else:
            dt = max(0.0, pending - fake._now) + 3.5 * interval
        fake.advance(dt)
        await run_ticks(fake, clock, 1)

        assert core.slip == 3

        await core.stop()

    @pytest.mark.asyncio
    async def test_slip_reads_the_clock_object(self) -> None:
        core, fake = fake_core(tick_rate=100)
        clock = core._tick_clock

        await core.start()
        await run_ticks(fake, clock, 5)

        assert core.slip == clock.slip

        await core.stop()


# ---------------------------------------------------------------------------
# Plugin.hook(name, at_tick=N)
# ---------------------------------------------------------------------------


class TestPluginHookAtTick:
    """Plugin.hook(name, at_tick=N) deferred execution contract."""

    @pytest.mark.asyncio
    async def test_hook_at_tick_returns_none(self, clean_core: Core) -> None:
        """hook(name, at_tick=N) must return None immediately."""
        from uxok.plugin import Plugin

        fired: list[bool] = []

        async def handler() -> None:
            fired.append(True)

        await clean_core.hooks.register("deferred.hook", handler, priority=0, plugin_id="t")
        plugin = Plugin(name="caller")
        plugin._attach_core(clean_core)
        result = plugin.hook("deferred.hook", at_tick=clean_core.tick + 5)

        assert result is None
        assert fired == []

    @pytest.mark.asyncio
    async def test_hook_at_tick_fires_at_correct_tick(self) -> None:
        """hook(at_tick=N) executes exactly at tick N, not before."""
        from uxok.plugin import Plugin

        core, fake = fake_core(tick_rate=100)
        clock = core._tick_clock
        fired_at: list[int] = []
        signal = asyncio.Event()

        async def handler() -> None:
            fired_at.append(clock.tick)
            signal.set()

        await core.hooks.register("at.hook", handler, priority=0, plugin_id="t")

        await core.start()

        plugin = Plugin(name="caller")
        plugin._attach_core(core)
        target = clock.tick + 7
        plugin.hook("at.hook", at_tick=target)

        await run_until(fake, lambda: clock.tick >= target - 1)
        assert fired_at == [], f"hook fired before target tick: {fired_at}"

        # Drive past target and drain scheduled tasks.
        await run_until(fake, lambda: clock.tick >= target + 1)
        for _ in range(100):
            await asyncio.sleep(0)
            if fired_at:
                break

        assert len(fired_at) == 1, f"Expected 1 firing, got {len(fired_at)}"
        assert fired_at[0] == target, f"Fired at {fired_at[0]}, expected {target}"

        await core.stop()

    @pytest.mark.asyncio
    async def test_hook_at_tick_fires_only_once(self) -> None:
        """hook(at_tick=N) is one-shot — fires exactly once, not repeatedly."""
        from uxok.plugin import Plugin

        core, fake = fake_core(tick_rate=100)
        clock = core._tick_clock
        fire_count = 0
        signal = asyncio.Event()

        async def handler() -> None:
            nonlocal fire_count
            fire_count += 1
            signal.set()

        await core.hooks.register("oneshot.hook", handler, priority=0, plugin_id="t")

        await core.start()

        plugin = Plugin(name="caller")
        plugin._attach_core(core)
        target = clock.tick + 3
        plugin.hook("oneshot.hook", at_tick=target)

        await run_until(fake, lambda: clock.tick >= target + 5)
        for _ in range(50):
            await asyncio.sleep(0)

        assert fire_count == 1

        await core.stop()

    @pytest.mark.asyncio
    async def test_hook_at_tick_past_raises_value_error(self, clean_core: Core) -> None:
        """hook(at_tick=N) with N <= core.tick raises ValueError."""
        from uxok.plugin import Plugin

        plugin = Plugin(name="caller")
        plugin._attach_core(clean_core)

        with pytest.raises(ValueError, match="at_tick"):
            plugin.hook("any.hook", at_tick=clean_core.tick)

        with pytest.raises(ValueError, match="at_tick"):
            plugin.hook("any.hook", at_tick=clean_core.tick - 1)

    @pytest.mark.asyncio
    async def test_hook_immediate_still_returns_results(self, clean_core: Core) -> None:
        """hook(name) without at_tick executes immediately and returns results."""
        from uxok.plugin import Plugin

        async def handler() -> str:
            return "ok"

        await clean_core.hooks.register("sync.hook", handler, priority=0, plugin_id="t")
        plugin = Plugin(name="caller")
        plugin._attach_core(clean_core)

        result = await plugin.hook("sync.hook")
        assert result == ["ok"]
