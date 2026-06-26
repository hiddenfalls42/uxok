"""
Stress test: high plugin count with concurrent event storms.

Simulates a production-scale system with many capability plugins, many flows,
and sustained concurrent event traffic.  Measures whether the architecture
holds under realistic load — not microbenchmarks, but the actual composition
pattern (events trigger flows, flows call hooks, hooks return results).

Target scenario: 100 capability plugins, 50 flow plugins, sustained burst
of 500+ events, all processed through the tick gate at 1000Hz.
"""

import asyncio
import time
from typing import Any

import pytest

from uxok import Core, Plugin
from uxok.plugin._decorators import event, hook
from uxok.protocols import Event

# ---------------------------------------------------------------------------
# Capability plugin factory — each provides one hook, one capability
# ---------------------------------------------------------------------------


def make_capability_plugin(core, name: str, hook_name: str, transform_fn):
    """Factory for one-capability-one-hook plugins."""

    class Cap(Plugin):
        def __init__(self):
            super().__init__(name=name, provides={name})

        @hook(hook_name)
        async def handle(self, **kwargs):
            data = kwargs.get("data", {})
            return transform_fn(data)

    return Cap()


# ---------------------------------------------------------------------------
# Flow plugin factory — trigger + hook pipeline
# ---------------------------------------------------------------------------


def make_flow_plugin(core, flow_name: str, trigger: str, hook_names: list[str]):
    """Factory for flow plugins that chain hooks."""

    class FlowPlugin(Plugin):
        def __init__(self):
            super().__init__(name=flow_name)
            self.executions = 0
            self.last_result = None
            self._event_handlers[trigger] = {
                "method": self.run_flow,
                "typed": False,
            }

        async def run_flow(self, event):
            data = event.data
            for hk in hook_names:
                result = await self.hook(hk, data=data, firstresult=True)
                if result is not None:
                    data = result
            self.executions += 1
            self.last_result = data
            await self.emit("complete", data)

    return FlowPlugin()


# ---------------------------------------------------------------------------
# Emitter plugin — fires events via public API
# ---------------------------------------------------------------------------


class EmitterPlugin(Plugin):
    def __init__(self):
        super().__init__(name="emitter", provides={"emitter"})
        self.sent = 0

    async def fire(self, event_name: str, data: Any):
        await self.emit(event_name, data)
        self.sent += 1


# ---------------------------------------------------------------------------
# Collector plugin — counts events it sees
# ---------------------------------------------------------------------------


class CollectorPlugin(Plugin):
    def __init__(self):
        super().__init__(name="collector", provides={"collector"})
        self.received = 0

    @event("sensor")
    async def count(self, event: Event):
        self.received += 1


# ===========================================================================
# TEST: Scale — 100 capabilities, 50 flows, 500 events
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.performance
async def test_100_capabilities_50_flows_500_events():
    """
    Registers 100 capability plugins (each providing one hook),
    50 flow plugins (each composing 3-5 hooks), fires 500 events,
    and verifies every flow executed for every event with zero data loss.
    """
    # Manual core: 152 plugins exceed the enforced default max_plugins=100,
    # so this test cannot use the default-config started_core fixture.
    core = Core(max_plugins=200)
    await core.start()
    try:
        await _run_scale_scenario(core)
    finally:
        await core.stop()


async def _run_scale_scenario(core) -> None:

    # --- Register 100 capability plugins ---
    t0 = time.monotonic()

    cap_plugins = []
    for i in range(100):
        transform = lambda d, i=i: {**d, f"cap_{i}": True}
        p = make_capability_plugin(
            core,
            name=f"cap_{i}",
            hook_name=f"op_{i}",
            transform_fn=transform,
        )
        await core.register_plugin(p)
        cap_plugins.append(p)

    reg_time = time.monotonic() - t0

    # --- Register 50 flow plugins, each chaining 3-5 hooks ---
    t1 = time.monotonic()

    flow_plugins = []
    for i in range(50):
        hooks_to_chain = [f"op_{(i * 3 + j) % 100}" for j in range(3 + i % 3)]
        fp = make_flow_plugin(
            core,
            flow_name=f"flow_{i}",
            trigger="sensor",
            hook_names=hooks_to_chain,
        )
        await core.register_plugin(fp)
        flow_plugins.append(fp)

    flow_reg_time = time.monotonic() - t1

    # --- Register emitter and collector ---
    emitter = EmitterPlugin()
    collector = CollectorPlugin()
    await core.register_plugin(emitter)
    await core.register_plugin(collector)

    # Let system settle
    await asyncio.sleep(0.1)

    total_plugins = len(await core.list())

    # --- Fire 500 events ---
    num_events = 500
    t2 = time.monotonic()

    for i in range(num_events):
        await emitter.fire("sensor", {"seq": i, "value": i * 0.1})

    emit_time = time.monotonic() - t2

    # Wait for all flows to process through the tick gate
    # 500 events × 50 flows × 3-5 hooks each = 75k-125k hook calls
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        total_execs = sum(f.executions for f in flow_plugins)
        if total_execs >= num_events * len(flow_plugins):
            break
        await asyncio.sleep(0.1)

    process_time = time.monotonic() - t2

    # --- Verify ---
    total_flow_executions = sum(f.executions for f in flow_plugins)
    expected_executions = num_events * len(flow_plugins)

    print(f"\n{'=' * 60}")
    print(f"RESULTS: {total_plugins} plugins, {num_events} events")
    print(f"{'=' * 60}")
    print(f"  Capability registration:  {reg_time:.3f}s ({reg_time / 100 * 1000:.1f}ms/plugin)")
    print(
        f"  Flow registration:        {flow_reg_time:.3f}s ({flow_reg_time / 50 * 1000:.1f}ms/flow)"
    )
    print(
        f"  Event emission (500):     {emit_time:.3f}s ({emit_time / num_events * 1000:.2f}ms/event)"
    )
    print(f"  Total processing time:    {process_time:.3f}s")
    print(f"  Flow executions:          {total_flow_executions:,} / {expected_executions:,}")
    print(f"  Hook calls (est):         ~{total_flow_executions * 4:,}")
    print(f"  Throughput:               {total_flow_executions / process_time:,.0f} flow-execs/sec")
    print(f"  Tick at end:              {core.tick}")
    print(f"  Final slip:               {core._tick_clock.slip}")

    assert total_flow_executions == expected_executions, (
        f"Expected {expected_executions} flow executions, got {total_flow_executions} "
        f"({expected_executions - total_flow_executions} lost)"
    )

    # Every flow should have executed exactly num_events times
    for fp in flow_plugins:
        assert fp.executions == num_events, (
            f"Flow {fp.metadata.name} executed {fp.executions} times, expected {num_events}"
        )

    assert core.state.name == "RUNNING"


# ===========================================================================
# TEST: Burst — concurrent event storm from multiple emitters
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.performance
async def test_concurrent_event_storm(started_core):
    """
    10 emitter plugins fire 100 events each simultaneously (1000 total),
    processed by 20 flows with 3 hooks each.  Verifies no events are lost
    under concurrent pressure.
    """
    core = started_core

    # 30 capabilities
    for i in range(30):
        transform = lambda d, i=i: {**d, f"t_{i}": True}
        p = make_capability_plugin(core, f"svc_{i}", f"op_{i}", transform)
        await core.register_plugin(p)

    # 20 flows, each chains 3 hooks
    flows = []
    for i in range(20):
        hooks = [f"op_{(i * 3 + j) % 30}" for j in range(3)]
        fp = make_flow_plugin(core, f"flow_{i}", "burst", hooks)
        await core.register_plugin(fp)
        flows.append(fp)

    # 10 emitter plugins
    emitters = []
    for i in range(10):
        e = Plugin(name=f"storm_{i}", provides={f"storm_{i}"})
        await core.register_plugin(e)
        emitters.append(e)

    await asyncio.sleep(0.1)

    # All 10 emitters fire 100 events concurrently
    events_per_emitter = 100
    t0 = time.monotonic()

    async def fire_burst(plugin, count):
        for i in range(count):
            await plugin.emit("burst", {"src": plugin.metadata.name, "seq": i})

    await asyncio.gather(*[fire_burst(e, events_per_emitter) for e in emitters])

    emit_time = time.monotonic() - t0
    total_events = len(emitters) * events_per_emitter

    # Wait for processing
    # Each event triggers 20 flows × 3 hooks = 60 hook calls
    # Total: 1000 events × 20 flows = 20,000 flow executions
    expected = total_events * len(flows)

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        total = sum(f.executions for f in flows)
        if total >= expected:
            break
        await asyncio.sleep(0.1)

    process_time = time.monotonic() - t0
    total_execs = sum(f.executions for f in flows)

    print(f"\n{'=' * 60}")
    print("CONCURRENT STORM: 10 emitters × 100 events × 20 flows")
    print(f"{'=' * 60}")
    print(f"  Burst emission time:      {emit_time:.3f}s")
    print(f"  Total processing time:    {process_time:.3f}s")
    print(f"  Flow executions:          {total_execs:,} / {expected:,}")
    print(f"  Throughput:               {total_execs / process_time:,.0f} flow-execs/sec")
    print(f"  Tick at end:              {core.tick}")
    print(f"  Final slip:               {core._tick_clock.slip}")

    assert total_execs == expected, (
        f"Lost {expected - total_execs} flow executions ({total_execs}/{expected})"
    )


# ===========================================================================
# TEST: Sustained load — events arriving continuously over 10 seconds
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.performance
async def test_sustained_load_10_seconds(started_core):
    """
    Continuous event stream at ~200 events/sec for 10 seconds (2000 events),
    processed by 10 flows.  Measures throughput stability over time and
    verifies zero data loss at the end.
    """
    core = started_core

    # 20 capabilities
    for i in range(20):
        transform = lambda d, i=i: {**d, f"x_{i}": True}
        p = make_capability_plugin(core, f"mod_{i}", f"step_{i}", transform)
        await core.register_plugin(p)

    # 10 flows
    flows = []
    for i in range(10):
        hooks = [f"step_{(i * 2 + j) % 20}" for j in range(4)]
        fp = make_flow_plugin(core, f"pipe_{i}", "tick", hooks)
        await core.register_plugin(fp)
        flows.append(fp)

    # Producer
    producer = Plugin(name="producer", provides={"producer"})
    await core.register_plugin(producer)

    await asyncio.sleep(0.1)

    # Sustained emission at ~200/sec for 10 seconds
    duration = 10.0
    target_rate = 200
    total_sent = 0
    throughput_samples = []

    t_start = time.monotonic()
    sample_start = t_start
    sample_count = 0

    while time.monotonic() - t_start < duration:
        await producer.emit("tick", {"seq": total_sent, "t": time.monotonic()})
        total_sent += 1
        sample_count += 1

        now = time.monotonic()
        if now - sample_start >= 1.0:
            throughput_samples.append(sample_count)
            sample_count = 0
            sample_start = now

        # Pace to target rate
        expected_time = total_sent / target_rate
        actual_elapsed = time.monotonic() - t_start
        if expected_time > actual_elapsed:
            await asyncio.sleep(expected_time - actual_elapsed)

    emit_done = time.monotonic()

    # Wait for all processing to complete
    expected_execs = total_sent * len(flows)
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        total = sum(f.executions for f in flows)
        if total >= expected_execs:
            break
        await asyncio.sleep(0.1)

    total_time = time.monotonic() - t_start
    total_execs = sum(f.executions for f in flows)

    print(f"\n{'=' * 60}")
    print(f"SUSTAINED LOAD: ~{target_rate} events/sec × {duration:.0f}s × {len(flows)} flows")
    print(f"{'=' * 60}")
    print(f"  Events sent:              {total_sent:,}")
    print(f"  Emission phase:           {emit_done - t_start:.3f}s")
    print(f"  Total time (inc. drain):  {total_time:.3f}s")
    print(f"  Flow executions:          {total_execs:,} / {expected_execs:,}")
    print(f"  Throughput:               {total_execs / total_time:,.0f} flow-execs/sec")
    if throughput_samples:
        print(f"  Emission rate samples:    {throughput_samples}")
        print(
            f"  Avg emission rate:        {sum(throughput_samples) / len(throughput_samples):.0f}/sec"
        )
    print(f"  Tick at end:              {core.tick}")
    print(f"  Final slip:               {core._tick_clock.slip}")

    assert total_execs == expected_execs, (
        f"Lost {expected_execs - total_execs} flow executions ({total_execs}/{expected_execs})"
    )


# ===========================================================================
# TEST: Registration churn under load — hot-load while events are flowing
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.performance
async def test_hot_reload_under_event_load(started_core):
    """
    Fires events continuously while hot-loading (unregister + register)
    flow plugins.  Verifies the system stays consistent and doesn't lose
    events or deadlock.
    """
    core = started_core

    # Base capabilities
    for i in range(10):
        transform = lambda d, i=i: {**d, f"h_{i}": True}
        p = make_capability_plugin(core, f"base_{i}", f"do_{i}", transform)
        await core.register_plugin(p)

    # Initial flows
    flows = []
    for i in range(5):
        hooks = [f"do_{(i + j) % 10}" for j in range(3)]
        fp = make_flow_plugin(core, f"live_{i}", "pulse", hooks)
        await core.register_plugin(fp)
        flows.append(fp)

    driver = Plugin(name="driver", provides={"driver"})
    await core.register_plugin(driver)

    await asyncio.sleep(0.1)

    # Fire events in background while churning flows
    stop_flag = asyncio.Event()
    events_sent = 0

    async def emit_loop():
        nonlocal events_sent
        while not stop_flag.is_set():
            await driver.emit("pulse", {"seq": events_sent})
            events_sent += 1
            await asyncio.sleep(0.005)

    emit_task = asyncio.create_task(emit_loop())

    # Churn: unregister and re-register flows 3 times each
    for cycle in range(3):
        for i in range(5):
            old = flows[i]
            await core.unregister_plugin(old.metadata.id, force=True)
            hooks = [f"do_{(i + cycle + j) % 10}" for j in range(3)]
            new_fp = make_flow_plugin(core, f"live_{i}", "pulse", hooks)
            await core.register_plugin(new_fp)
            flows[i] = new_fp
        await asyncio.sleep(0.1)

    # Let events flow a bit more
    await asyncio.sleep(0.5)
    stop_flag.set()
    await emit_task

    # Drain
    await asyncio.sleep(0.3)

    total_execs = sum(f.executions for f in flows)

    print(f"\n{'=' * 60}")
    print("HOT RELOAD UNDER LOAD")
    print(f"{'=' * 60}")
    print(f"  Events sent:              {events_sent:,}")
    print(f"  Flow executions (final):  {total_execs:,}")
    print("  Reload cycles:            3 × 5 flows = 15 swaps")
    print(f"  Tick at end:              {core.tick}")
    print(f"  Final slip:               {core._tick_clock.slip}")

    # We can't assert exact counts because flows were churning,
    # but we can verify the system is consistent and didn't crash
    assert total_execs > 0, "Final flows should have processed some events"
    assert core.state.name == "RUNNING"

    # Verify all flows are registered and functional
    plugin_list = await core.list()
    for i in range(5):
        found = plugin_list.by_name(f"live_{i}")
        assert found is not None, f"Flow live_{i} should still be registered"
