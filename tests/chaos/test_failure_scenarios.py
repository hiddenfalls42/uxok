"""
Chaos engineering tests for uxok Framework.

These tests inject failures to verify system resilience and error isolation.
"""

import asyncio

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.helpers import EventCollectingPlugin, wait_until
from tests.strategies import failure_rates
from uxok import Core, Plugin
from uxok.protocols import Event


class FaultyPlugin(Plugin):
    """Plugin that fails in various ways for chaos testing."""

    def __init__(self, failure_mode: str = None, failure_rate: float = 0.0, name: str = None):
        super().__init__(name=name)
        self.failure_mode = failure_mode
        self.failure_rate = failure_rate
        self.events_processed = 0

    async def on_start(self):
        if self.failure_mode == "start_failure":
            raise ValueError("Simulated startup failure")

        # Subscribe to test events
        await self.core.events.subscribe("test.*", self._handle_event, self.metadata.id)

    async def on_stop(self):
        if self.failure_mode == "stop_failure":
            raise RuntimeError("Simulated shutdown failure")

    async def _handle_event(self, event: Event):
        """Handle events with potential failures."""
        self.events_processed += 1

        # Inject random failures based on rate
        if self.failure_rate > 0 and self.events_processed % int(1.0 / self.failure_rate) == 0:
            if self.failure_mode == "event_handler_failure":
                raise ConnectionError("Simulated event handler failure")


@pytest.mark.asyncio
@given(total_plugins=st.integers(min_value=5, max_value=15), crash_rate=failure_rates())
@settings(max_examples=15, deadline=4000)
async def test_plugin_crash_isolation_property(total_plugins, crash_rate):
    """
    Property: Plugin crashes are isolated from the core system.

    Tests that when plugins fail during startup, the core remains stable
    and other plugins continue functioning.
    """
    core = Core()

    try:
        await core.start()

        # Create mix of stable and crashing plugins
        crash_count = int(total_plugins * crash_rate)
        stable_count = total_plugins - crash_count

        plugins_created = []

        # Create crashing plugins
        for i in range(crash_count):
            plugin = FaultyPlugin(failure_mode="start_failure", name=f"crash_plugin_{i}")
            plugins_created.append(("crash", plugin))

        # Create stable plugins
        for i in range(stable_count):
            plugin = FaultyPlugin(failure_mode=None, name=f"stable_plugin_{i}")
            plugins_created.append(("stable", plugin))

        # Attempt to register all plugins
        successful_registrations = 0
        failed_registrations = 0
        stable_plugins = []

        for plugin_type, plugin in plugins_created:
            try:
                result = await core.register_plugin(plugin)
                if result:
                    await plugin.start()
                    successful_registrations += 1
                    if plugin_type == "stable":
                        stable_plugins.append(plugin)
                else:
                    failed_registrations += 1
            except Exception:
                failed_registrations += 1

        # Verify crash isolation
        expected_successful = stable_count
        assert successful_registrations == expected_successful, (
            f"Expected {expected_successful} successful registrations, got {successful_registrations}"
        )

        # Core should remain running
        assert core.state.name == "RUNNING", (
            f"Core crashed due to plugin failures: {core.state.name}"
        )

        # Stable plugins should still be functional
        for plugin in stable_plugins:
            # Should be accessible
            retrieved = (await core.list()).by_id(plugin.metadata.id)
            assert retrieved is not None and retrieved.name == plugin.metadata.name

            # Should be able to process events
            await core.events.publish(
                Event(name="test.event", data={"test": "data"}, timestamp=0.0, tick=0, slip=0)
            )

        # Stable plugins should have processed events
        await wait_until(lambda: sum(p.events_processed for p in stable_plugins) > 0)
        events_processed = sum(p.events_processed for p in stable_plugins)
        assert events_processed > 0, "Stable plugins should have processed events"

    finally:
        await core.stop()


@pytest.mark.asyncio
@given(handler_count=st.integers(min_value=5, max_value=15), failure_rate=failure_rates())
@settings(max_examples=15, deadline=3000)
async def test_event_handler_exception_isolation_property(handler_count, failure_rate):
    """
    Property: Event handler exceptions don't block other handlers.

    Tests that when event handlers fail, other handlers for the same event continue executing.
    """
    core = Core()

    try:
        await core.start()

        # Create event collector to verify all handlers run
        collector = EventCollectingPlugin(name="collector")
        await core.register_plugin(collector)
        await collector.start()

        # Subscribe collector to test event
        await core.events.subscribe("test.event", collector._collect_event, collector.metadata.id)

        # Subscribe failing handlers
        failing_handlers = []
        for i in range(handler_count):
            plugin = FaultyPlugin(
                failure_mode="event_handler_failure"
                if i < int(handler_count * failure_rate)
                else None,
                failure_rate=failure_rate,
                name=f"handler_plugin_{i}",
            )
            await core.register_plugin(plugin)
            await plugin.start()

            # Subscribe to test event
            await core.events.subscribe("test.event", plugin._handle_event, plugin.metadata.id)
            failing_handlers.append(plugin)

        # Publish test event
        await core.events.publish(
            Event(name="test.event", data={"test": "chaos"}, timestamp=0.0, tick=0, slip=0)
        )

        # Wait for processing
        expected_successful = handler_count - int(handler_count * failure_rate)
        await wait_until(
            lambda: (
                sum(1 for p in failing_handlers if p.events_processed > 0)
                >= expected_successful * 0.8
                and len(collector.events_received) >= 1
            )
        )

        # Count successful handlers
        successful_handlers = sum(1 for p in failing_handlers if p.events_processed > 0)

        # Some handlers should have succeeded despite others failing
        assert successful_handlers >= expected_successful * 0.8, (
            f"Too many handlers failed: {successful_handlers}/{handler_count} successful"
        )

        # Collector should have received the event
        assert len(collector.events_received) >= 1, (
            "Event collector should have received the test event"
        )

    finally:
        await core.stop()


@pytest.mark.asyncio
@given(
    hook_count=st.integers(min_value=3, max_value=10),
    failure_indices=st.lists(st.integers(min_value=0, max_value=9), max_size=10),
)
@settings(max_examples=15, deadline=3000)
async def test_hook_execution_failure_resilience_property(hook_count, failure_indices):
    """
    Property: Hook execution failures are isolated.

    Tests that when some hooks fail, successful hooks still execute and return results.
    """
    # Filter failure_indices to only include valid indices
    failure_indices = [i for i in failure_indices if i < hook_count]

    core = Core()

    try:
        await core.start()

        # Register hooks with some designed to fail
        hook_results = []

        async def failing_hook(hook_id: int, should_fail: bool, firstresult=False, **kwargs):
            if should_fail:
                raise ValueError(f"Hook {hook_id} intentionally failed")
            hook_results.append(f"hook_{hook_id}_success")
            return {"result": f"hook_{hook_id}", "status": "success"}

        # Register hooks - create a list of (hook_func, should_fail) pairs first
        hook_specs = [(i, i in failure_indices) for i in range(hook_count)]

        # Register hooks with properly captured variables (fix closure issue)
        for hook_id, should_fail in hook_specs:

            async def hook_func(firstresult=False, hid=hook_id, sfail=should_fail, **kwargs):
                return await failing_hook(hid, sfail, firstresult=firstresult, **kwargs)

            await core.hooks.register("test_hook", hook_func, priority=10)

        # Execute hooks
        results = await core._hook_system.execute("test_hook", input="test")

        # Count successful results
        successful_results = [
            r for r in results if isinstance(r, dict) and r.get("status") == "success"
        ]

        # Should have results from non-failing hooks
        expected_successful = hook_count - len(set(failure_indices))
        assert len(successful_results) == expected_successful, (
            f"Expected {expected_successful} successful hook results, got {len(successful_results)}"
        )

        # Verify hook_results tracking matches
        assert len(hook_results) == expected_successful, (
            f"Hook execution tracking mismatch: {len(hook_results)} != {expected_successful}"
        )

        # Results should be in correct order (all successful hooks)
        for i, result in enumerate(successful_results):
            expected_hook_id = [j for j in range(hook_count) if j not in failure_indices][i]
            assert result["result"] == f"hook_{expected_hook_id}", (
                f"Hook result order incorrect: {result}"
            )

    finally:
        await core.stop()


@pytest.mark.asyncio
async def test_core_recovery_from_event_bus_failure(started_core):
    """
    Test: Core recovers from event bus subsystem failures.

    This test specifically targets event bus failure scenarios and recovery.
    Not property-based due to specific failure injection requirements.
    """
    core = started_core

    # Create a plugin that will help monitor system health
    monitor = EventCollectingPlugin(name="monitor")
    await core.register_plugin(monitor)
    await monitor.start()

    # Subscribe to system events
    await core.events.subscribe("*", monitor._collect_event, monitor.metadata.id)

    # Publish normal events to establish baseline
    for i in range(10):
        await core.events.publish(
            Event(name="baseline.event", data={"index": i}, timestamp=0.0, tick=0, slip=0)
        )

    # Verify baseline functionality
    await wait_until(
        lambda: sum(1 for e in monitor.events_received if e.name == "baseline.event") >= 10
    )
    baseline_events = [e for e in monitor.events_received if e.name == "baseline.event"]
    assert len(baseline_events) == 10, "Baseline events should be processed"

    # Simulate event bus stress (rapid publishing)
    stress_tasks = []
    for i in range(50):  # High load
        task = asyncio.create_task(
            core.events.publish(
                Event(name="stress.event", data={"stress_id": i}, timestamp=0.0, tick=0, slip=0)
            )
        )
        stress_tasks.append(task)

    # Wait for stress test completion (all stress events processed by monitor)
    await asyncio.gather(*stress_tasks)
    await wait_until(
        lambda: sum(1 for e in monitor.events_received if e.name == "stress.event") >= 50
    )

    # Verify system recovered
    assert core.state.name == "RUNNING", (
        f"Core should remain running after event bus stress: {core.state.name}"
    )

    # Verify event processing still works
    await core.events.publish(
        Event(name="recovery.test", data={"recovery": True}, timestamp=0.0, tick=0, slip=0)
    )

    await wait_until(lambda: any(e.name == "recovery.test" for e in monitor.events_received))

    # Check recovery event was processed
    recovery_events = [e for e in monitor.events_received if e.name == "recovery.test"]
    assert len(recovery_events) == 1, "System should process events after recovery"

    # Verify plugin is still functional
    retrieved_monitor = (await core.list()).by_name("monitor")
    assert retrieved_monitor is not None and retrieved_monitor.name == "monitor", (
        "Monitor plugin should remain accessible"
    )
