"""
Concurrency tests for uxok Framework.

These tests verify thread-safety and race condition handling under concurrent load.
"""

import asyncio
import dataclasses

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.helpers import EventCollectingPlugin
from tests.strategies import valid_core_configs
from uxok import Core, Plugin
from uxok.protocols import Event


class ConcurrencyTestPlugin(Plugin):
    """Plugin for testing concurrent operations."""

    def __init__(self, name: str = None, provides: set[str] = None, requires: set[str] = None):
        super().__init__(name=name, provides=provides or set(), requires=requires or set())
        self.registration_order = -1
        self.capability_access_count = 0

    async def on_start(self):
        # Record capability access attempts
        for capability in self.metadata.requires:
            try:
                provider = (await self.core.list()).capability.provides(capability).first()
                self.capability_access_count += 1
            except Exception:
                pass  # Expected in some race condition tests


@pytest.mark.asyncio
@given(
    plugin_count=st.integers(min_value=5, max_value=20),
    concurrent_batches=st.integers(min_value=2, max_value=5),
)
@settings(max_examples=15, deadline=4000)
async def test_concurrent_plugin_registration_safety_property(plugin_count, concurrent_batches):
    """
    Property: Concurrent plugin registration is safe.

    Tests that registering multiple plugins concurrently either succeeds for all or fails cleanly,
    with no partial registration states or race conditions.
    """
    core = Core()

    try:
        await core.start()

        # Create plugins to register
        plugins = []
        for i in range(plugin_count):
            plugin = ConcurrencyTestPlugin(
                name=f"concurrent_plugin_{i}",
                provides={f"capability_{i}"},
                requires=set(),  # No dependencies to avoid complexity
            )
            plugins.append(plugin)

        # Register plugins in concurrent batches
        successful_registrations = 0
        failed_registrations = 0

        for batch_start in range(0, plugin_count, concurrent_batches):
            batch_end = min(batch_start + concurrent_batches, plugin_count)
            batch_plugins = plugins[batch_start:batch_end]

            # Register batch concurrently
            registration_tasks = [core.register_plugin(plugin) for plugin in batch_plugins]

            results = await asyncio.gather(*registration_tasks, return_exceptions=True)

            # Count successes and failures
            for result in results:
                if isinstance(result, Exception):
                    failed_registrations += 1
                else:
                    successful_registrations += 1

        # Verify system consistency
        total_registrations = successful_registrations + failed_registrations
        assert total_registrations == plugin_count, (
            f"Registration count mismatch: {total_registrations} != {plugin_count}"
        )

        # Verify all successful plugins are accessible
        accessible_plugins = 0
        for plugin in plugins:
            try:
                retrieved = (await core.list()).by_id(plugin.metadata.id)
                if retrieved is not None and retrieved.name == plugin.metadata.name:
                    accessible_plugins += 1
            except Exception:
                pass  # Plugin might not have been registered successfully

        assert accessible_plugins == successful_registrations, (
            f"Accessibility mismatch: {accessible_plugins} accessible, {successful_registrations} registered"
        )

        # Verify core remains in running state
        assert core.state.name == "RUNNING", (
            f"Core state corrupted after concurrent registration: {core.state.name}"
        )

    finally:
        await core.stop()


@pytest.mark.asyncio
@given(config=valid_core_configs())
@settings(max_examples=10, deadline=5000)
async def test_concurrent_hot_reload_property(config):
    """
    Property: Concurrent hot-reloads work safely.

    Tests that multiple plugins can be hot-reloaded simultaneously without deadlocks
    or state corruption.
    """
    core = Core(**dataclasses.asdict(config))

    try:
        await core.start()

        # Create initial plugins
        initial_plugins = []
        for i in range(5):  # Fixed number for manageable test
            plugin = ConcurrencyTestPlugin(name=f"reload_test_{i}", provides={f"service_{i}"})
            await core.register_plugin(plugin)
            await plugin.start()
            initial_plugins.append(plugin)

        # Define event publishing function
        async def publish_events(core, count):
            for i in range(count):
                await core.events.publish(
                    Event(name="test.event", data={"sequence": i}, timestamp=0.0, tick=0, slip=0)
                )
                await asyncio.sleep(0.001)  # Small delay

        # Start background event publishing to create load
        event_task = asyncio.create_task(publish_events(core, 100))

        # Perform concurrent hot-reloads
        async def reload_plugin(plugin_idx: int):
            old_plugin = initial_plugins[plugin_idx]

            # Unregister old
            await core.unregister_plugin(old_plugin.metadata.id)

            # Register new with same name (simulating reload)
            new_plugin = ConcurrencyTestPlugin(
                name=f"reload_test_{plugin_idx}", provides={f"service_{plugin_idx}"}
            )
            await core.register_plugin(new_plugin)
            await new_plugin.start()

            return new_plugin

        # Reload all plugins concurrently
        reload_tasks = [reload_plugin(i) for i in range(5)]
        reloaded_plugins = await asyncio.gather(*reload_tasks)

        # Wait for background events to complete
        await event_task

        # Verify all plugins are functional after reload
        for i, plugin in enumerate(reloaded_plugins):
            # Plugin should be accessible
            retrieved = (await core.list()).by_id(plugin.metadata.id)
            assert retrieved is not None and retrieved.name == plugin.metadata.name

            # Provided capability should be accessible
            capability_provider = (await core.list()).capability.provides(f"service_{i}").first()
            assert (
                capability_provider is not None and capability_provider.name == plugin.metadata.name
            )

        # System should remain stable
        assert core.state.name == "RUNNING"

    finally:
        await core.stop()


@pytest.mark.asyncio
@given(
    num_capabilities=st.integers(min_value=3, max_value=10),
    concurrent_requests=st.integers(min_value=5, max_value=20),
)
@settings(max_examples=15, deadline=3000)
async def test_capability_resolution_race_condition_property(num_capabilities, concurrent_requests):
    """
    Property: Capability resolution handles concurrent requests safely.

    Tests that multiple consumers requesting the same capability concurrently
    all get the same provider without race conditions.
    """
    core = Core()

    try:
        await core.start()

        # Create capability providers
        providers = []
        for i in range(num_capabilities):
            provider = ConcurrencyTestPlugin(name=f"provider_{i}", provides={f"capability_{i}"})
            await core.register_plugin(provider)
            await provider.start()
            providers.append(provider)

        # Create consumers that will request capabilities concurrently
        async def request_capability(capability_idx: int) -> str:
            """Request a specific capability and return provider name."""
            provider = (
                (await core.list()).capability.provides(f"capability_{capability_idx}").first()
            )
            return provider.name

        # Make concurrent requests for each capability
        request_tasks = []
        for cap_idx in range(num_capabilities):
            # Multiple consumers requesting the same capability
            for _ in range(concurrent_requests // num_capabilities + 1):
                request_tasks.append(request_capability(cap_idx))

        # Execute all requests concurrently
        results = await asyncio.gather(*request_tasks)

        # Verify all requests for the same capability got the same provider
        for cap_idx in range(num_capabilities):
            expected_provider = f"provider_{cap_idx}"
            cap_results = [
                result
                for i, result in enumerate(results)
                if i % (concurrent_requests // num_capabilities + 1)
                == cap_idx % (concurrent_requests // num_capabilities + 1)
                and (i // (concurrent_requests // num_capabilities + 1)) == cap_idx
            ]

            # All requests for this capability should return the same provider
            unique_providers = set(cap_results)
            assert len(unique_providers) == 1, (
                f"Capability {cap_idx} resolved to multiple providers: {unique_providers}"
            )
            assert list(unique_providers)[0] == expected_provider, (
                f"Capability {cap_idx} resolved to wrong provider: {list(unique_providers)[0]}"
            )

    finally:
        await core.stop()


@pytest.mark.asyncio
@given(
    publishers=st.integers(min_value=3, max_value=10),
    events_per_publisher=st.integers(min_value=50, max_value=200),
)
@settings(max_examples=10, deadline=5000)
async def test_event_bus_under_concurrent_load_property(publishers, events_per_publisher):
    """
    Property: Event bus handles concurrent publishers correctly.

    Tests that multiple publishers can publish concurrently without losing events,
    creating duplicates, or corrupting per-publisher ordering.
    """
    core = Core()

    try:
        await core.start()

        # Create event collector plugin
        collector = EventCollectingPlugin(name="collector")
        await core.register_plugin(collector)
        await collector.start()

        # Subscribe to all events
        await core.events.subscribe("*", collector._collect_event, collector.metadata.id)

        # Create concurrent publishers
        async def publish_events(publisher_id: int, event_count: int):
            """Publish events from a specific publisher."""
            events_published = []
            for i in range(event_count):
                event = Event(
                    name=f"publisher_{publisher_id}.event",
                    data={"publisher": publisher_id, "sequence": i},
                    timestamp=float(publisher_id * event_count + i) / 1000.0,
                )
                await core.events.publish(event)
                events_published.append((publisher_id, i))
            return events_published

        # Start concurrent publishing
        publish_tasks = [
            publish_events(publisher_id, events_per_publisher) for publisher_id in range(publishers)
        ]

        published_events = await asyncio.gather(*publish_tasks)

        await asyncio.sleep(0.5)

        # Flatten published events for verification
        all_published = []
        for publisher_events in published_events:
            all_published.extend(publisher_events)

        # Verify collected events
        collected_events = collector.events_received

        # Filter out internal framework events (core.tick_slip, etc.)
        user_events = [e for e in collected_events if not e.name.startswith("core.")]

        # Check total count
        assert len(user_events) == publishers * events_per_publisher, (
            f"Event count mismatch: {len(collected_events)} collected, {publishers * events_per_publisher} published"
        )

        # Verify per-publisher ordering is preserved.
        # NOTE: the constitution guarantees only CAUSAL ordering, not global
        # cross-publisher order. Per-publisher monotonicity holds here because
        # each publisher awaits its emits serially and asyncio schedules the
        # resulting dispatch tasks FIFO — an asyncio scheduling detail, not a
        # contractual guarantee. Do not read this as a global-ordering promise.
        for publisher_id in range(publishers):
            publisher_events = [
                (e.data["publisher"], e.data["sequence"])
                for e in user_events
                if e.data.get("publisher") == publisher_id
            ]

            # Should have exactly events_per_publisher events
            assert len(publisher_events) == events_per_publisher, (
                f"Publisher {publisher_id} has {len(publisher_events)} events, expected {events_per_publisher}"
            )

            # Should be in sequence order
            sequences = [seq for _, seq in publisher_events]
            expected_sequences = list(range(events_per_publisher))
            assert sequences == expected_sequences, (
                f"Publisher {publisher_id} sequence corrupted: {sequences} != {expected_sequences}"
            )

        # Verify no duplicate events
        event_signatures = [(e.data["publisher"], e.data["sequence"]) for e in user_events]
        unique_signatures = set(event_signatures)
        assert len(event_signatures) == len(unique_signatures), (
            f"Duplicate events detected: {len(event_signatures)} total, {len(unique_signatures)} unique"
        )

    finally:
        await core.stop()
