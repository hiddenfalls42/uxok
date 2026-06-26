"""Tests for the event bus: publish/subscribe, error isolation, tick-stamping.

Fire-and-forget contract (post gate redesign):
  await bus.publish(event) launches each subscriber as a task and returns.
  Subscribers have NOT run yet when publish() returns.  To observe delivery,
  await the bus's own _dispatch_tasks or wait on a deterministic asyncio.Event
  set by the handler.

Test organisation (simplest → most adversarial):

  Bootstrap contract              — bare _EventBus(); tasks need a yield to run
  Subscription management         — subscribe / unsubscribe / unsubscribe_plugin
  Sync-callback path              — non-async handlers in fire-and-forget tasks
  Error isolation                 — handler errors, plugin_error attribution
  Nested-error path               — publish of core.plugin_error itself fails
  Tick-stamping                   — exact tick/slip values via FakeTime Core
  Concurrent dispatch via Core    — started_core; delivery via wait_until
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from tests.fake_time import FakeTime, install_fake_time, run_ticks
from tests.helpers import EventCollectingPlugin, StubPlugin, wait_until
from uxok import Core
from uxok.events._bus import _EventBus
from uxok.protocols import Event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fake_core_100() -> tuple[Core, FakeTime]:
    """100 Hz Core wired to FakeTime. Must be called before core.start()."""
    core = Core(tick_rate=100, hook_precaching="disabled")
    fake = FakeTime()
    install_fake_time(core, fake)
    return core, fake


async def _drain(bus: _EventBus) -> None:
    """Yield until all current dispatch tasks complete."""
    tasks = list(bus._dispatch_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    # Extra yield to let any tasks created by error-path re-publish settle.
    for _ in range(5):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Bootstrap contract
# ---------------------------------------------------------------------------


class TestBootstrapContract:
    """Bare _EventBus() — tasks require a yield before they run."""

    @pytest.mark.asyncio
    async def test_publish_no_subscribers_returns_true(self) -> None:
        bus = _EventBus()
        assert await bus.publish(Event("no.sub", {})) is True

    @pytest.mark.asyncio
    async def test_publish_delivers_to_async_subscriber(self) -> None:
        """publish() schedules the handler as a task; it runs after a yield."""
        bus = _EventBus()
        received: list[str] = []

        async def handler(event: Event) -> None:
            received.append(event.name)

        await bus.subscribe("test.event", handler)
        await bus.publish(Event("test.event", {"key": "value"}))
        await _drain(bus)

        assert received == ["test.event"]

    @pytest.mark.asyncio
    async def test_bootstrap_events_carry_tick_zero(self) -> None:
        """Events published with no clock or clock.tick==0 are NOT re-stamped."""
        bus = _EventBus()  # no clock
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await bus.subscribe("boot.event", handler)
        await bus.publish(Event("boot.event", {}))
        await _drain(bus)

        assert len(received) == 1
        assert received[0].tick == 0
        assert received[0].slip == 0

    @pytest.mark.asyncio
    async def test_bootstrap_events_not_restamped_when_clock_tick_is_zero(self) -> None:
        """Clock present but tick==0: event is NOT re-stamped (guard on line 46)."""
        clock = MagicMock()
        clock.tick = 0
        clock.slip = 0

        bus = _EventBus(clock=clock)
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await bus.subscribe("boot.event", handler)
        await bus.publish(Event("boot.event", {}))
        await _drain(bus)

        assert len(received) == 1
        assert received[0].tick == 0
        assert received[0].slip == 0

    @pytest.mark.asyncio
    async def test_publish_is_fire_and_forget(self) -> None:
        """publish() returns immediately; handlers run after a yield.

        Under the new model there is NO synchronous-delivery guarantee.
        """
        bus = _EventBus()
        completed: list[bool] = []

        async def handler(event: Event) -> None:
            await asyncio.sleep(0.01)
            completed.append(True)

        await bus.subscribe("slow", handler)

        # After publish() returns the handler has NOT completed yet.
        await bus.publish(Event("slow", {}))
        assert completed == [], "handler must not complete before the caller yields"

        # After draining it completes.
        await _drain(bus)
        assert completed == [True]


# ---------------------------------------------------------------------------
# Subscription management
# ---------------------------------------------------------------------------


class TestSubscriptionManagement:
    @pytest.mark.asyncio
    async def test_unsubscribe_removes_subscriber_and_stops_delivery(self) -> None:
        """After unsubscribe: count drops to 0 AND a subsequent publish is not delivered."""
        bus = _EventBus()
        call_count = 0

        async def handler(event: Event) -> None:
            nonlocal call_count
            call_count += 1

        sub_id = await bus.subscribe("test.event", handler)
        await bus.publish(Event("test.event", {}))
        await _drain(bus)
        assert call_count == 1

        await bus.unsubscribe(sub_id)
        assert bus._subscriptions.count() == 0

        await bus.publish(Event("test.event", {}))
        await _drain(bus)
        assert call_count == 1  # no second delivery

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent_id_is_safe(self) -> None:
        """Unsubscribing an unknown ID must not raise."""
        bus = _EventBus()
        await bus.unsubscribe("does-not-exist")

    @pytest.mark.asyncio
    async def test_unsubscribe_plugin_removes_subscriptions_and_stops_delivery(self) -> None:
        """unsubscribe_plugin: count drops to 0, subsequent publish delivers nothing."""
        bus = _EventBus()
        pid = uuid4()
        call_count = 0

        async def handler(event: Event) -> None:
            nonlocal call_count
            call_count += 1

        await bus.subscribe("test.event", handler, plugin_id=pid)
        await bus.publish(Event("test.event", {}))
        await _drain(bus)
        assert call_count == 1

        await bus.unsubscribe_plugin(pid)
        assert bus._subscriptions.count() == 0

        await bus.publish(Event("test.event", {}))
        await _drain(bus)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_subscribe_multiple_handlers_same_event(self) -> None:
        """Two handlers on the same event — both receive every publish."""
        bus = _EventBus()
        received_a: list[str] = []
        received_b: list[str] = []

        async def handler_a(event: Event) -> None:
            received_a.append(event.name)

        async def handler_b(event: Event) -> None:
            received_b.append(event.name)

        await bus.subscribe("multi.event", handler_a)
        await bus.subscribe("multi.event", handler_b)
        await bus.publish(Event("multi.event", {}))
        await _drain(bus)

        assert received_a == ["multi.event"]
        assert received_b == ["multi.event"]

    @pytest.mark.asyncio
    async def test_wildcard_pattern_matches_multiple_events(self) -> None:
        """A glob-pattern subscriber receives events matching the pattern."""
        bus = _EventBus()
        received: list[str] = []

        async def handler(event: Event) -> None:
            received.append(event.name)

        await bus.subscribe("sensor.*", handler)
        await bus.publish(Event("sensor.temperature", {}))
        await bus.publish(Event("sensor.pressure", {}))
        await bus.publish(Event("other.event", {}))
        await _drain(bus)

        assert received == ["sensor.temperature", "sensor.pressure"]


# ---------------------------------------------------------------------------
# Sync-callback path
# ---------------------------------------------------------------------------


class TestSyncCallbackPath:
    """Plain (non-async) subscribers run inside fire-and-forget tasks."""

    @pytest.mark.asyncio
    async def test_sync_subscriber_receives_event(self) -> None:
        """A sync (non-async) subscriber receives the published event."""
        bus = _EventBus()
        received: list[str] = []

        def sync_handler(event: Event) -> None:
            received.append(event.name)

        await bus.subscribe("sync.event", sync_handler)
        await bus.publish(Event("sync.event", {"payload": 42}))
        await _drain(bus)

        assert received == ["sync.event"]

    @pytest.mark.asyncio
    async def test_sync_and_async_subscribers_coexist(self) -> None:
        """Sync and async subscribers on the same event both receive the event."""
        bus = _EventBus()
        sync_received: list[str] = []
        async_received: list[str] = []

        def sync_handler(event: Event) -> None:
            sync_received.append(event.name)

        async def async_handler(event: Event) -> None:
            async_received.append(event.name)

        await bus.subscribe("mixed.event", sync_handler)
        await bus.subscribe("mixed.event", async_handler)
        await bus.publish(Event("mixed.event", {}))
        await _drain(bus)

        assert sync_received == ["mixed.event"]
        assert async_received == ["mixed.event"]

    @pytest.mark.asyncio
    async def test_sync_subscriber_error_is_isolated(self) -> None:
        """A crashing sync subscriber does NOT prevent other subscribers from receiving."""
        bus = _EventBus()
        surviving_received: list[str] = []

        def crashing_sync(event: Event) -> None:
            raise RuntimeError("sync crash")

        async def surviving_handler(event: Event) -> None:
            surviving_received.append(event.name)

        await bus.subscribe("sync.error.event", crashing_sync)
        await bus.subscribe("sync.error.event", surviving_handler)
        await bus.publish(Event("sync.error.event", {}))
        await _drain(bus)

        assert surviving_received == ["sync.error.event"]

    @pytest.mark.asyncio
    async def test_sync_subscriber_error_produces_plugin_error_event(self) -> None:
        """A crashing sync subscriber emits core.plugin_error with correct attribution."""
        bus = _EventBus()
        pid = uuid4()
        error_events: list[dict] = []

        def crashing_sync(event: Event) -> None:
            raise ValueError("sync error message")

        async def on_error(event: Event) -> None:
            error_events.append(event.data)

        await bus.subscribe("sync.fail.event", crashing_sync, plugin_id=pid)
        await bus.subscribe("core.plugin_error", on_error)
        await bus.publish(Event("sync.fail.event", {}))
        await _drain(bus)

        assert len(error_events) == 1
        assert error_events[0]["plugin_id"] == str(pid)
        assert error_events[0]["source"] == "event_handler"
        assert error_events[0]["error_type"] == "ValueError"
        assert error_events[0]["event_name"] == "sync.fail.event"


# ---------------------------------------------------------------------------
# Async error isolation
# ---------------------------------------------------------------------------


class TestErrorIsolation:
    @pytest.mark.asyncio
    async def test_callback_error_does_not_affect_others(self) -> None:
        """A crashing handler is isolated; other handlers run and core.plugin_error fires."""
        bus = _EventBus()
        pid = uuid4()
        delivery_order: list[str] = []
        error_events: list[dict] = []

        async def bad_handler(event: Event) -> None:
            raise RuntimeError("handler crash")

        async def good_handler_a(event: Event) -> None:
            delivery_order.append("a")

        async def good_handler_b(event: Event) -> None:
            delivery_order.append("b")

        async def on_error(event: Event) -> None:
            error_events.append(event.data)

        await bus.subscribe("test.event", bad_handler, plugin_id=pid)
        await bus.subscribe("test.event", good_handler_a)
        await bus.subscribe("test.event", good_handler_b)
        await bus.subscribe("core.plugin_error", on_error)

        await bus.publish(Event("test.event", {}))
        await _drain(bus)

        assert sorted(delivery_order) == ["a", "b"]
        assert len(error_events) == 1
        assert error_events[0]["source"] == "event_handler"
        assert error_events[0]["error_type"] == "RuntimeError"

    @pytest.mark.asyncio
    async def test_handler_failure_publishes_plugin_error_with_exact_payload(self) -> None:
        """A crashing handler produces a core.plugin_error with full attribution."""
        bus = _EventBus()
        pid = uuid4()
        error_events: list[dict] = []

        async def bad_handler(event: Event) -> None:
            raise RuntimeError("handler crash")

        async def on_error(event: Event) -> None:
            error_events.append(event.data)

        await bus.subscribe("test.event", bad_handler, plugin_id=pid)
        await bus.subscribe("core.plugin_error", on_error)
        await bus.publish(Event("test.event", {}))
        await _drain(bus)

        assert len(error_events) == 1
        assert error_events[0]["plugin_id"] == str(pid)
        assert error_events[0]["source"] == "event_handler"
        assert error_events[0]["event_name"] == "test.event"
        assert error_events[0]["error_type"] == "RuntimeError"
        assert error_events[0]["error"] == "handler crash"

    @pytest.mark.asyncio
    async def test_failure_handling_plugin_error_does_not_loop(self) -> None:
        """A handler that crashes ON core.plugin_error must not recurse.

        Guard at _bus.py: event.name == 'core.plugin_error' → return immediately.
        """
        bus = _EventBus()
        calls: list[str] = []

        async def crashing_error_handler(event: Event) -> None:
            calls.append(event.name)
            raise RuntimeError("meta-crash")

        async def bad_handler(event: Event) -> None:
            raise RuntimeError("original crash")

        await bus.subscribe("core.plugin_error", crashing_error_handler)
        await bus.subscribe("test.event", bad_handler)
        await bus.publish(Event("test.event", {}))
        await _drain(bus)

        # The error handler ran exactly once; its crash was NOT re-reported.
        assert calls == ["core.plugin_error"]


# ---------------------------------------------------------------------------
# Nested error-publish failure path
# ---------------------------------------------------------------------------


class TestNestedErrorPublishFailure:
    """Force the inner publish(core.plugin_error) call itself to fail and verify
    it is swallowed (logged at DEBUG), not propagated to the caller.
    """

    @pytest.mark.asyncio
    async def test_failed_plugin_error_publish_is_swallowed_not_propagated(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When publishing core.plugin_error itself raises, the error is swallowed."""
        import logging

        bus = _EventBus()

        async def bad_handler(event: Event) -> None:
            raise RuntimeError("trigger error")

        await bus.subscribe("original.event", bad_handler)

        # Make get_subscribers raise for 'core.plugin_error'.
        original_get_subscribers = bus._subscriptions.get_subscribers

        def get_subscribers_patched(event_name: str):
            if event_name == "core.plugin_error":
                raise RuntimeError("injected: get_subscribers exploded")
            return original_get_subscribers(event_name)

        bus._subscriptions.get_subscribers = get_subscribers_patched  # type: ignore[method-assign]

        with caplog.at_level(logging.DEBUG, logger="uxok.events._bus"):
            result = await bus.publish(Event("original.event", {}))
            await _drain(bus)

        assert result is True

        debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("core.plugin_error" in msg for msg in debug_messages), (
            f"Expected a DEBUG log about failed core.plugin_error publish; got: {debug_messages}"
        )


# ---------------------------------------------------------------------------
# Tick-stamping
# ---------------------------------------------------------------------------


class TestTickStamping:
    """Exact tick and slip values stamped at publish time via FakeTime Core."""

    @pytest.mark.asyncio
    async def test_event_stamped_with_clock_tick_and_slip_at_publish_time(self) -> None:
        """Publish at known tick T with slip S ⇒ event.tick==T and event.slip==S."""
        core, fake = fake_core_100()
        clock = core._tick_clock
        received: list[Event] = []
        signal = asyncio.Event()

        async def handler(event: Event) -> None:
            received.append(event)
            signal.set()

        await core.events.subscribe("stamp.probe", handler)
        await core.start()

        await run_ticks(fake, clock, 5)
        publish_tick = clock.tick  # == 5

        await core.events.publish(Event("stamp.probe", {}))

        # Wait for the fire-and-forget task to deliver.
        await asyncio.wait_for(signal.wait(), timeout=2.0)

        assert len(received) == 1
        assert received[0].tick == publish_tick
        assert received[0].slip == 0

        await core.stop()

    @pytest.mark.asyncio
    async def test_event_stamped_with_nonzero_slip(self) -> None:
        """Publish immediately after a stall boundary: event.slip matches the stall magnitude."""
        core, fake = fake_core_100()
        clock = core._tick_clock
        received: list[Event] = []
        signal = asyncio.Event()

        async def handler(event: Event) -> None:
            received.append(event)
            signal.set()

        await core.events.subscribe("slip.probe", handler)
        await core.start()

        await run_ticks(fake, clock, 5)

        await asyncio.sleep(0)
        pending = fake.next_deadline()
        assert pending is not None
        fake.advance((pending - fake._now) + 3.5 * clock._tick_interval)

        await run_ticks(fake, clock, 1)
        assert clock.slip == 3

        await core.events.publish(Event("slip.probe", {}))
        await asyncio.wait_for(signal.wait(), timeout=2.0)

        assert len(received) == 1
        assert received[0].slip == 3

        await core.stop()

    @pytest.mark.asyncio
    async def test_bootstrap_event_not_stamped_after_start(self) -> None:
        """Events published BEFORE core.start() carry tick==0."""
        core = Core(tick_rate=100, hook_precaching="disabled")
        received: list[Event] = []
        signal = asyncio.Event()

        async def handler(event: Event) -> None:
            received.append(event)
            signal.set()

        await core.events.subscribe("bootstrap.event", handler)
        # Publish BEFORE start — clock.tick is 0, no re-stamping.
        await core.events.publish(Event("bootstrap.event", {}))
        await asyncio.wait_for(signal.wait(), timeout=2.0)

        assert len(received) == 1
        assert received[0].tick == 0
        assert received[0].slip == 0

        await core.start()
        await core.stop()


# ---------------------------------------------------------------------------
# Concurrent dispatch via real started Core
# ---------------------------------------------------------------------------


class TestConcurrentDispatchThroughCore:
    """Drive events through a real started Core — fire-and-forget delivery."""

    @pytest.mark.asyncio
    async def test_event_delivered_through_running_core(self, started_core: Core) -> None:
        """Publish via a running Core; the callback is delivered eventually."""
        core = started_core
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await core.events.subscribe("gate.test.event", handler)
        await core.events.publish(Event("gate.test.event", {"data": "delivered"}))

        await wait_until(lambda: len(received) == 1)
        assert received[0].name == "gate.test.event"
        assert received[0].data == {"data": "delivered"}

    @pytest.mark.asyncio
    async def test_multiple_subscribers_all_receive(self, started_core: Core) -> None:
        """All three subscribers on the same event receive it."""
        core = started_core
        received: list[str] = []

        for label in ("x", "y", "z"):

            async def handler(event: Event, _l: str = label) -> None:
                received.append(_l)

            await core.events.subscribe("broadcast.event", handler)

        await core.events.publish(Event("broadcast.event", {}))
        await wait_until(lambda: len(received) == 3)

        assert sorted(received) == ["x", "y", "z"]

    @pytest.mark.asyncio
    async def test_unsubscribe_plugin_through_core_removes_delivery(
        self, started_core: Core
    ) -> None:
        """After unsubscribe_plugin, no further delivery."""
        core = started_core
        call_count = 0
        pid = uuid4()

        async def handler(event: Event) -> None:
            nonlocal call_count
            call_count += 1

        await core.events.subscribe("unsub.plugin.event", handler, plugin_id=pid)
        await core.events.publish(Event("unsub.plugin.event", {}))
        await wait_until(lambda: call_count == 1)

        await core.events.unsubscribe_plugin(pid)
        await core.events.publish(Event("unsub.plugin.event", {}))

        await asyncio.sleep(0.05)

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_error_in_handler_produces_plugin_error_event(self, started_core: Core) -> None:
        """A crashing subscriber emits core.plugin_error."""
        core = started_core
        pid = uuid4()
        error_events: list[dict] = []

        async def bad_handler(event: Event) -> None:
            raise ValueError("crash")

        async def on_error(event: Event) -> None:
            error_events.append(event.data)

        await core.events.subscribe("gate.error.event", bad_handler, plugin_id=pid)
        await core.events.subscribe("core.plugin_error", on_error)
        await core.events.publish(Event("gate.error.event", {}))

        await wait_until(lambda: len(error_events) == 1)

        assert error_events[0]["plugin_id"] == str(pid)
        assert error_events[0]["source"] == "event_handler"
        assert error_events[0]["error_type"] == "ValueError"

    @pytest.mark.asyncio
    async def test_event_collecting_plugin_receives_wildcard_events(
        self, started_core: Core
    ) -> None:
        """EventCollectingPlugin subscribed to '*' receives every published event."""
        core = started_core
        collector = EventCollectingPlugin(name="collector", subscribe_to="*")
        await core.register_plugin(collector)

        await core.events.publish(Event("arbitrary.event.one", {"seq": 1}))
        await core.events.publish(Event("arbitrary.event.two", {"seq": 2}))

        await wait_until(
            lambda: len([e for e in collector.events_received if "arbitrary.event" in e.name]) >= 2
        )

        names = [e.name for e in collector.events_received if "arbitrary.event" in e.name]
        assert "arbitrary.event.one" in names
        assert "arbitrary.event.two" in names

    @pytest.mark.asyncio
    async def test_tick_stamp_gt_zero_through_real_core(self, started_core: Core) -> None:
        """Events published through a started Core carry a non-zero tick stamp."""
        core = started_core
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await core.events.subscribe("live.event", handler)

        await wait_until(lambda: core.tick > 0)

        await core.events.publish(Event("live.event", {}))
        await wait_until(lambda: len(received) == 1)

        assert received[0].tick > 0

    @pytest.mark.asyncio
    async def test_plugin_unregistered_events_gone_via_unsubscribe_owner(
        self, started_core: Core
    ) -> None:
        """After plugin unregistration, its subscriptions are cleaned up."""
        core = started_core
        call_count = 0

        class CountingPlugin(StubPlugin):
            async def on_start(self) -> None:
                await self.core.events.subscribe(
                    "counting.event", self._handle, self.metadata.id, owner=self
                )

            async def _handle(self, event: Event) -> None:
                nonlocal call_count
                call_count += 1

        plugin = CountingPlugin(name="counter_plugin")
        await core.register_plugin(plugin)

        await core.events.publish(Event("counting.event", {}))
        await wait_until(lambda: call_count == 1)

        plugin_id = plugin.metadata.id
        await core.unregister_plugin(plugin_id)

        await core.events.publish(Event("counting.event", {}))
        await asyncio.sleep(0.05)

        assert call_count == 1


# ---------------------------------------------------------------------------
# drain() — the production teardown primitive (wired into Core.stop())
# ---------------------------------------------------------------------------


class TestDrain:
    """The bus's own drain() cancels in-flight dispatch tasks and empties the set."""

    @pytest.mark.asyncio
    async def test_drain_cancels_in_flight_dispatch_and_empties_set(self) -> None:
        bus = _EventBus()
        started = asyncio.Event()

        async def never_finishes(_event: Event) -> None:
            started.set()
            await asyncio.Event().wait()  # blocks forever until cancelled

        await bus.subscribe("slow.event", never_finishes)
        await bus.publish(Event("slow.event", {}))

        # The handler is in flight (tracked, not yet complete).
        await asyncio.wait_for(started.wait(), timeout=1.0)
        in_flight = list(bus._dispatch_tasks)
        assert len(in_flight) == 1

        await bus.drain()

        assert bus._dispatch_tasks == set()
        assert in_flight[0].cancelled()


# ---------------------------------------------------------------------------
# Demand-driven emission — has_subscribers + mute at the bus level
# ---------------------------------------------------------------------------


class TestMuteAndHasSubscribers:
    """The bus exposes has_subscribers/mute/unmute and drops muted events."""

    @pytest.mark.asyncio
    async def test_has_subscribers_reflects_subscriptions(self) -> None:
        bus = _EventBus()
        assert bus.has_subscribers("a.b") is False
        await bus.subscribe("a.*", lambda e: None)
        assert bus.has_subscribers("a.b") is True

    @pytest.mark.asyncio
    async def test_mute_drops_dispatch_at_source(self) -> None:
        bus = _EventBus()
        received: list[str] = []

        async def handler(e: Event) -> None:
            received.append(e.name)

        await bus.subscribe("noisy.event", handler)
        bus.mute("noisy.*")
        assert await bus.publish(Event("noisy.event", {})) is True
        await _drain(bus)
        assert received == []  # muted before dispatch

    @pytest.mark.asyncio
    async def test_muted_event_reports_no_subscribers(self) -> None:
        bus = _EventBus()
        await bus.subscribe("noisy.event", lambda e: None)
        bus.mute("noisy.*")
        assert bus.has_subscribers("noisy.event") is False

    @pytest.mark.asyncio
    async def test_unmute_restores_dispatch(self) -> None:
        bus = _EventBus()
        received: list[str] = []

        async def handler(e: Event) -> None:
            received.append(e.name)

        await bus.subscribe("noisy.event", handler)
        bus.mute("noisy.*")
        bus.unmute("noisy.*")
        await bus.publish(Event("noisy.event", {}))
        await _drain(bus)
        assert received == ["noisy.event"]

    @pytest.mark.asyncio
    async def test_publish_muted_at_nonzero_tick_returns_true_no_dispatch(self) -> None:
        """publish() short-circuits for a muted event even when clock.tick > 0.

        Guards the reordered restamp path: the muted branch must not touch the
        clock or allocate a restamped Event.
        """
        clock = MagicMock()
        clock.tick = 5
        clock.slip = 0

        bus = _EventBus(clock=clock)
        received: list[str] = []

        async def handler(e: Event) -> None:
            received.append(e.name)

        await bus.subscribe("muted.topic", handler)
        bus.mute("muted.*")

        result = await bus.publish(Event("muted.topic", {}))
        await _drain(bus)

        assert result is True
        assert received == []

    @pytest.mark.asyncio
    async def test_publish_unsubscribed_at_nonzero_tick_returns_true_no_dispatch(self) -> None:
        """publish() short-circuits for an unsubscribed event even when clock.tick > 0.

        Guards the reordered restamp path: the empty-subscribers branch must not
        allocate a restamped Event or invoke the clock.
        """
        clock = MagicMock()
        clock.tick = 7
        clock.slip = 1

        bus = _EventBus(clock=clock)

        result = await bus.publish(Event("nobody.listening", {"x": 1}))
        await _drain(bus)

        assert result is True
        # clock.tick was read — only for attribute access inside the guard,
        # but no Event was constructed and no subscriber was dispatched.
        # The MagicMock records attribute access; we just assert no tasks ran.
        assert not bus._dispatch_tasks

    @pytest.mark.asyncio
    async def test_publish_subscribed_at_nonzero_tick_delivers_and_restamps(self) -> None:
        """Regression: normal subscribed publish still delivers and restamps at tick>0."""
        clock = MagicMock()
        clock.tick = 3
        clock.slip = 0

        bus = _EventBus(clock=clock)
        received: list[Event] = []
        signal = asyncio.Event()

        async def handler(e: Event) -> None:
            received.append(e)
            signal.set()

        await bus.subscribe("live.event", handler)
        await bus.publish(Event("live.event", {"val": 42}))
        await asyncio.wait_for(signal.wait(), timeout=2.0)

        assert len(received) == 1
        assert received[0].tick == 3  # restamped from clock
        assert received[0].data == {"val": 42}
