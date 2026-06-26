"""
Event System Property-Based Tests.

This module tests the event system using property-based testing to ensure
comprehensive coverage of event ordering, delivery guarantees, and cleanup
characteristics through minimal, focused property definitions.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import run_state_machine_as_test

from tests.helpers import wait_until
from tests.state_machines import CoreStateMachine
from tests.strategies import valid_events
from uxok import Core, Plugin
from uxok.protocols import Event


def _user_events(names: list[str]) -> list[str]:
    """Filter out kernel-internal events (core.*) from a delivery log."""
    return [name for name in names if not name.startswith("core.")]


class EventTrackingPlugin(Plugin):
    """Plugin for tracking event delivery and order."""

    def __init__(self, name=None):
        unique_name = name or f"event_tracker_{id(self)}"
        super().__init__(name=unique_name)
        self.delivered_events: list[Event] = []
        self.delivery_order: list[str] = []
        self.delivery_timestamps: list[float] = []

    async def on_start(self):
        """Register event handlers for tracking."""
        await self.core.events.subscribe("*", self.track_event, self.metadata.id)

    async def track_event(self, event: Event):
        """Track all events delivered to this plugin."""
        self.delivered_events.append(event)
        self.delivery_order.append(event.name)
        self.delivery_timestamps.append(time.time())


class TestEventSystemProperties:
    """Property-based tests for event system invariants."""

    @pytest.mark.asyncio
    @given(events=st.lists(valid_events(), min_size=1, max_size=50))
    @settings(max_examples=20, deadline=2000)
    async def test_event_order_preservation(self, events):
        """
        Property: Event bus preserves delivery order for a single subscriber
        with no drops and no duplicates.

        The tick gate executes queued subscriber callbacks serially at tick
        boundaries, so the delivered list must equal the published list exactly
        (same length, same order, no extras).
        """
        core = Core()
        await core.start()

        try:
            tracker = EventTrackingPlugin(name="order_tracker")
            await core.register_plugin(tracker)

            for event in events:
                await core.events.publish(event)

            # Wait for all user events to arrive; kernel core.* events are
            # interleaved but filtered before assertion.
            expected = _user_events([event.name for event in events])
            await wait_until(lambda: len(_user_events(tracker.delivery_order)) >= len(expected))

            delivered = _user_events(tracker.delivery_order)

            # Order preserved
            assert delivered == expected
            # No drops and no duplicates
            assert len(delivered) == len(expected)

        finally:
            await core.stop()

    @pytest.mark.asyncio
    @given(
        event_count=st.integers(min_value=10, max_value=200),
        subscriber_count=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=10, deadline=5000)
    async def test_event_bus_no_memory_leaks(self, event_count, subscriber_count):
        """
        Property: Unregistering all subscriber plugins removes every subscription.

        After every plugin is unregistered the subscription manager must hold
        exactly zero live subscriptions (count() == 0) and the by-id index must
        be empty.  No refcount theater — the assertion is exact and deterministic.
        """
        core = Core()
        await core.start()

        try:
            plugins = []
            for i in range(subscriber_count):
                plugin = EventTrackingPlugin(name=f"subscriber_{i}")
                await core.register_plugin(plugin)
                plugins.append(plugin)

            events = [Event(name=f"test_{j % 10}", data={"index": j}) for j in range(event_count)]
            for event in events:
                await core.events.publish(event)

            # Every subscriber receives every event before we tear down.
            await wait_until(
                lambda: all(len(_user_events(p.delivery_order)) >= event_count for p in plugins),
                timeout=3.0,
            )

            for plugin in plugins:
                await core.unregister_plugin(plugin.metadata.id)
            plugins.clear()

            # Invariant: unregister removes every subscription — count is exact.
            sub_manager = core.events._subscriptions
            assert sub_manager.count() == 0
            assert len(sub_manager._subscriptions_by_id) == 0

        finally:
            await core.stop()

    @pytest.mark.asyncio
    @given(
        event_count=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=20, deadline=2000)
    async def test_fanout_exactly_once_delivery(self, event_count):
        """
        Property: Every subscriber receives every matching event exactly once,
        in publish order, across all subscribers (fan-out).

        Three independent plugins subscribe to the same event pattern.  Each
        must receive exactly event_count deliveries, indexed 0..event_count-1
        in order — no drops, no duplicates, no reordering.
        """
        core = Core()
        await core.start()

        try:
            plugins = []
            for i in range(3):
                plugin = FanoutTestPlugin(name=f"fanout_subscriber_{i}")
                await core.register_plugin(plugin)
                plugins.append(plugin)

            for i in range(event_count):
                event = Event(name="fanout_test", data={"index": i})
                await core.events.publish(event)

            # Wait for all plugins to accumulate all events.
            await wait_until(
                lambda: sum(len(p.results) for p in plugins) >= event_count * 3,
                timeout=3.0,
            )

            for plugin in plugins:
                # Exactly event_count deliveries — no drops, no dupes.
                assert len(plugin.results) == event_count
                # Publish order preserved for each subscriber independently.
                assert [e.data["index"] for e in plugin.results] == list(range(event_count))

        finally:
            await core.stop()

    @pytest.mark.asyncio
    @given(
        concurrent_publishers=st.integers(min_value=1, max_value=10),
        events_per_publisher=st.integers(min_value=1, max_value=20),
    )
    @settings(max_examples=15, deadline=5000)
    async def test_concurrent_event_publishing(self, concurrent_publishers, events_per_publisher):
        """
        Property: Concurrent event publishing maintains consistency.

        Tests consistency under concurrent load.
        """
        core = Core()
        await core.start()

        try:
            tracker = EventTrackingPlugin(name="concurrent_tracker")
            await core.register_plugin(tracker)

            async def publish_events(publisher_id):
                for i in range(events_per_publisher):
                    event = Event(
                        name=f"concurrent_{publisher_id}",
                        data={"publisher": publisher_id, "index": i},
                    )
                    await core.events.publish(event)

            tasks = [asyncio.create_task(publish_events(i)) for i in range(concurrent_publishers)]
            await asyncio.gather(*tasks)

            expected_total = concurrent_publishers * events_per_publisher

            def delivered():
                return [e for e in tracker.delivered_events if e.name.startswith("concurrent_")]

            await wait_until(lambda: len(delivered()) >= expected_total, timeout=3.0)
            assert len(delivered()) == expected_total

            # Invariant: No events should be lost or duplicated
            received_events = set()
            for event in delivered():
                event_key = (event.name, event.data["publisher"], event.data["index"])
                received_events.add(event_key)

            assert len(received_events) == expected_total

        finally:
            await core.stop()

    @pytest.mark.asyncio
    @given(
        event_types=st.sets(
            st.text(min_size=1, max_size=20).filter(lambda s: not s.startswith("core.")),
            min_size=1,
            max_size=10,
        ),
        data_variations=st.dictionaries(st.text(), st.integers() | st.text()),
    )
    @settings(max_examples=20, deadline=2000)
    async def test_event_data_integrity(self, event_types, data_variations):
        """
        Property: Event name, data, and timestamp are preserved through delivery.

        Note: the bus re-stamps ``tick`` and ``slip`` with the live clock at
        publish time (see EventBus.publish), so those fields are intentionally
        NOT asserted to match the values the publisher set.
        """
        core = Core()
        await core.start()

        try:
            integrity_plugin = EventIntegrityPlugin()
            await core.register_plugin(integrity_plugin)

            test_events = []
            for event_type in event_types:
                event = Event(name=event_type, data=data_variations)
                test_events.append(event)
                await core.events.publish(event)

            def received():
                return [
                    e for e in integrity_plugin.received_events if not e.name.startswith("core.")
                ]

            await wait_until(lambda: len(received()) >= len(test_events), timeout=3.0)

            # Invariant: All events should be delivered with intact payloads
            assert len(received()) == len(test_events)

            for expected, got in zip(test_events, received()):
                assert expected.name == got.name
                assert expected.data == got.data
                assert expected.timestamp == got.timestamp

        finally:
            await core.stop()

    @pytest.mark.asyncio
    @given(
        base_load=st.integers(min_value=1, max_value=100),
        load_multiplier=st.floats(
            min_value=1.0, max_value=5.0, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=10, deadline=5000)
    async def test_event_bus_delivery_under_load(self, base_load, load_multiplier):
        """
        Property: Every published event is delivered exactly once under load.

        Correctness invariant only — no throughput assertion.  Speed is not a
        correctness property; use the @performance-marked variant below for
        a smoke check.
        """
        core = Core()
        await core.start()

        try:
            perf_plugin = EventPerformancePlugin()
            await core.register_plugin(perf_plugin)

            event_count = int(base_load * load_multiplier)
            events = [Event(name="performance_test", data={"index": i}) for i in range(event_count)]

            for event in events:
                await core.events.publish(event)

            # Invariant: every published event is delivered exactly once.
            await wait_until(lambda: perf_plugin.received_count >= event_count, timeout=5.0)
            assert perf_plugin.received_count == event_count

        finally:
            await core.stop()

    @pytest.mark.asyncio
    @pytest.mark.performance
    @given(
        base_load=st.integers(min_value=50, max_value=200),
    )
    @settings(max_examples=5, deadline=10000)
    async def test_event_bus_publish_throughput(self, base_load):
        """
        Perf smoke: publishing at least 100 events/second.

        Deselected by default (requires ``-m performance``).  Failure here
        indicates a regression in the publish hot path, not a logic error.
        """
        core = Core()
        await core.start()

        try:
            perf_plugin = EventPerformancePlugin()
            await core.register_plugin(perf_plugin)

            events = [Event(name="performance_test", data={"index": i}) for i in range(base_load)]

            start_time = time.perf_counter()
            for event in events:
                await core.events.publish(event)
            duration = time.perf_counter() - start_time

            events_per_second = base_load / duration if duration > 0 else float("inf")
            assert events_per_second > 100

        finally:
            await core.stop()


class FanoutTestPlugin(Plugin):
    """Plugin for testing fan-out delivery across independent subscribers."""

    def __init__(self, name):
        super().__init__(name=name)
        self.results: list[Event] = []

    async def on_start(self):
        await self.core.events.subscribe("fanout_test", self.handle_event, self.metadata.id)

    async def handle_event(self, event: Event):
        self.results.append(event)


class EventIntegrityPlugin(Plugin):
    """Plugin for testing event data integrity."""

    def __init__(self):
        super().__init__(name="integrity_checker")
        self.received_events: list[Event] = []

    async def on_start(self):
        await self.core.events.subscribe("*", self.check_integrity, self.metadata.id)

    async def check_integrity(self, event: Event):
        import copy

        event_copy = copy.deepcopy(event)
        self.received_events.append(event_copy)


class EventPerformancePlugin(Plugin):
    """Plugin for performance tracking."""

    def __init__(self):
        super().__init__(name="performance_tracker")
        self.received_count = 0

    async def on_start(self):
        await self.core.events.subscribe(
            "performance_test", self.track_performance, self.metadata.id
        )

    async def track_performance(self, event: Event):
        self.received_count += 1


# =============================================================================
# STATE MACHINE TESTS
# =============================================================================


def test_core_event_lifecycle_via_state_machine():
    """Test core event lifecycle using state machine exploration."""
    run_state_machine_as_test(CoreStateMachine)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
