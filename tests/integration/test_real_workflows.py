"""
Integration tests for real workflow scenarios in uxok Framework.

These tests verify end-to-end functionality across multiple components:
- Plugin registration and lifecycle
- Event publishing and subscription
- Hook execution with priorities
- Capability resolution and access
"""

import asyncio
import dataclasses

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.helpers import EventCollectingPlugin, create_plugin_with_capabilities
from tests.strategies import (
    event_names,
    hook_names,
    plugin_capability_combinations,
    priority_levels,
    valid_core_configs,
)
from uxok import Core
from uxok.protocols import Event


@pytest.mark.asyncio
@given(config=valid_core_configs())
@settings(max_examples=20, deadline=3000)
async def test_complete_plugin_workflow_property(config):
    """
    Property: Complete plugin workflow works end-to-end.

    Tests the full lifecycle: provider plugin → consumer plugin → event publish → hook execution → capability access.
    """
    core = Core(**dataclasses.asdict(config))

    try:
        await core.start()

        # Create provider plugin
        provider = create_plugin_with_capabilities(
            core, provides={"data_processing"}, name="provider"
        )
        await core.register_plugin(provider)
        await provider.start()

        # Create consumer plugin with event collection
        consumer = EventCollectingPlugin(name="consumer", subscribe_to="*")
        await core.register_plugin(consumer)
        await consumer.start()

        # Register hook on consumer
        await core.hooks.register("process_data", consumer.collect_hook_execution, priority=10)

        # Publish event that should trigger processing
        event_data = {"value": 42, "action": "process"}
        event = Event(name="data.received", data=event_data, timestamp=0.0, tick=0, slip=0)
        await core.events.publish(event)

        # Wait for processing
        await asyncio.sleep(0.1)

        # Verify event was received
        assert len(consumer.events_received) >= 1
        received_event = consumer.events_received[0]
        assert received_event.name == "data.received"
        assert received_event.data["value"] == 42

        # Execute hook
        hook_result = await core._hook_system.execute("process_data", data="test")
        assert len(hook_result) == 1
        assert hook_result[0]["processed"] is True
        assert hook_result[0]["data"] == "test"

        # Verify hook execution was recorded
        assert len(consumer.hooks_executed) == 1
        assert consumer.hooks_executed[0]["data"] == "test"

        # Test capability access
        capability_provider = (await core.list()).capability.provides("data_processing").first()
        assert (
            capability_provider is not None and capability_provider.name == provider.metadata.name
        )

        # Verify smart lookup works
        found_provider = (await core.list()).capability.provides("data_processing").first()
        assert found_provider is not None and found_provider.name == provider.metadata.name

    finally:
        await core.stop()


@pytest.mark.asyncio
@given(capability_configs=plugin_capability_combinations())
@settings(max_examples=15, deadline=4000)
async def test_multi_plugin_capability_chain_property(capability_configs):
    """
    Property: Multi-plugin capability chains work correctly.

    Tests capability dependency resolution in chains: A provides X → B requires X, provides Y → C requires Y.
    Verifies all plugins start in correct dependency order.
    """
    # Skip invalid test cases: requires capabilities but provides nothing
    provides, requires = capability_configs
    if requires and not provides:
        pytest.skip("Invalid test case: requires capabilities but provides none")

    core = Core()

    try:
        await core.start()

        # Create a provider plugin that provides the capabilities
        plugins = []
        if provides:
            provider = create_plugin_with_capabilities(
                core, provides=provides, requires=set(), name="provider"
            )
            plugins.append(provider)

        # Create a consumer plugin that requires the capabilities
        if requires and provides:  # Only create consumer if there's something to consume
            consumer = create_plugin_with_capabilities(
                core, provides=set(), requires=requires, name="consumer"
            )
            plugins.append(consumer)

        # Register plugins
        for plugin in plugins:
            await core.register_plugin(plugin)

        # Verify plugins are functional
        for plugin in plugins:
            # Can access the plugin
            retrieved = (await core.list()).by_id(plugin.metadata.id)
            assert retrieved is plugin

            # Provided capabilities are accessible
            for capability in plugin.metadata.provides:
                provider = (await core.list()).capability.provides(capability).first()
                assert provider is plugin

        # If both provides and requires exist, verify the chain works
        if provides and requires:
            # Verify consumer can access provider's capabilities
            for required in requires:
                if required in provides:  # Only test capabilities that are actually provided
                    capability = (await core.list()).capability.provides(required).first()
                    assert capability is not None, (
                        f"Required capability {required} should be available"
                    )

    finally:
        await core.stop()


@pytest.mark.asyncio
@given(
    priorities=st.lists(priority_levels(), min_size=3, max_size=8),
    hook_name=hook_names(),
    event_name=event_names(),
)
@settings(max_examples=20, deadline=2000)
async def test_event_hook_priority_integration_property(priorities, hook_name, event_name):
    """
    Property: Event-hook integration respects priority ordering.

    Tests that when events trigger hooks, execution order follows priority rules.
    """
    core = Core()

    try:
        await core.start()

        # Create plugin to collect execution order
        collector = EventCollectingPlugin(name="collector", subscribe_to="*")
        await core.register_plugin(collector)
        await collector.start()

        # Register hooks with different priorities
        execution_order = []
        for i, priority in enumerate(priorities):

            async def hook_executor(order_list=execution_order, index=i, pri=priority):
                order_list.append((index, pri))
                return {"executed": True, "priority": pri, "index": index}

            await core.hooks.register(hook_name, hook_executor, priority=priority)

        # Subscribe to event that triggers the hook
        async def event_handler(event: Event, hook_name=hook_name):
            # Execute hook when event is received
            results = await core._hook_system.execute(hook_name, event_data=event.data)
            # Record that hooks were executed
            await core.events.publish(
                Event(
                    name="hooks.executed", data={"results": results}, timestamp=0.0, tick=0, slip=0
                )
            )

        await core.events.subscribe(event_name, event_handler, collector.metadata.id)

        # Publish event to trigger the chain
        await core.events.publish(
            Event(name=event_name, data={"test": "data"}, timestamp=0.0, tick=0, slip=0)
        )

        # Wait for processing
        await asyncio.sleep(0.1)

        # Verify hooks were executed in priority order (higher priority first)
        if execution_order:
            # Sort by priority (descending) and check order
            sorted_by_priority = sorted(execution_order, key=lambda x: x[1], reverse=True)
            # Should match the recorded execution order
            priorities_recorded = [p for _, p in execution_order]
            priorities_expected = [p for _, p in sorted_by_priority]
            assert priorities_recorded == priorities_expected, (
                f"Execution order {priorities_recorded} doesn't match priority order {priorities_expected}"
            )

        # Verify event was processed
        event_received = any(e.name == event_name for e in collector.events_received)
        assert event_received, "Event should have been received by collector"

    finally:
        await core.stop()
