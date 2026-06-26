"""Tests for Plugin.emit() verbatim name publishing and Event.source field.

Covers the breaking change introduced in the emit() auto-prefix removal:
- emit(name, data) publishes the name verbatim (no plugin-name prefix)
- Event.source is set to the emitting plugin's name
- Direct core.events.publish() leaves source=None
- Wildcard and dotted-name subscriptions still work
- Deferred emit(at_tick=N) also sets source correctly

The bus preserves Event.source through the tick/slip re-stamp on publish, so
source holds whether or not the tick system is running.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.helpers import wait_until
from uxok import Core
from uxok.plugin import Plugin
from uxok.protocols import Event

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def core() -> Core:
    """Unstarted Core; tests that need a running core call start() themselves."""
    return Core(tick_rate=100, hook_precaching="disabled")


# ---------------------------------------------------------------------------
# Verbatim name — no prefix
# ---------------------------------------------------------------------------


class TestVerbatimName:
    """emit() publishes exactly the name given, nothing prepended."""

    @pytest.mark.asyncio
    async def test_flat_name_received_by_flat_subscriber(self, core: Core) -> None:
        """A flat emit("reading") is received on subscribe("reading")."""
        await core.start()
        sensor = Plugin(name="sensor")
        await core.register_plugin(sensor)

        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await core.events.subscribe("reading", handler)
        await sensor.emit("reading", {"value": 42})
        await wait_until(lambda: len(received) == 1)

        assert received[0].name == "reading"

        await core.stop()

    @pytest.mark.asyncio
    async def test_old_prefixed_subscriber_does_not_receive(self, core: Core) -> None:
        """A subscriber on the old auto-prefix form receives nothing.

        Before the change, emit("reading") on plugin "sensor" published
        "sensor.reading". That form no longer exists.
        """
        await core.start()
        sensor = Plugin(name="sensor")
        await core.register_plugin(sensor)

        leaked: list[Event] = []

        async def on_prefixed(event: Event) -> None:
            leaked.append(event)

        await core.events.subscribe("sensor.reading", on_prefixed)
        await sensor.emit("reading", {"value": 1})
        # Allow enough time for the event to propagate if it ever did
        await asyncio.sleep(0.05)

        assert leaked == [], (
            "Subscriber on old prefixed form 'sensor.reading' must receive nothing; "
            f"got {len(leaked)} event(s)"
        )

        await core.stop()


# ---------------------------------------------------------------------------
# Event.source field
# ---------------------------------------------------------------------------


class TestEventSource:
    """Event.source is set to the emitting plugin's name by Plugin.emit().

    The bus preserves source through the tick/slip re-stamp on publish, so this
    holds whether or not the tick system is running.
    """

    @pytest.mark.asyncio
    async def test_emit_stamps_source_with_plugin_name(self, core: Core) -> None:
        """emit() sets event.source to the emitting plugin's name."""
        await core.start()
        sensor = Plugin(name="sensor")
        await core.register_plugin(sensor)

        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await core.events.subscribe("reading", handler)
        await sensor.emit("reading", {"value": 7})
        await wait_until(lambda: len(received) == 1)

        assert received[0].source == "sensor"

        await core.stop()

    @pytest.mark.asyncio
    async def test_direct_publish_leaves_source_none(self, core: Core) -> None:
        """core.events.publish(Event(...)) with no source leaves source=None."""
        await core.start()

        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await core.events.subscribe("x", handler)
        await core.events.publish(Event("x", {"payload": True}))
        await wait_until(lambda: len(received) == 1)

        assert received[0].source is None

        await core.stop()

    @pytest.mark.asyncio
    async def test_direct_publish_explicit_source_preserved_before_clock_ticks(
        self,
    ) -> None:
        """source set manually on Event is preserved when the clock hasn't ticked.

        This tests the tick==0 path: no stamp reconstruction occurs, so the
        original Event (with its source) is dispatched unchanged.
        """
        core = Core(hook_precaching="disabled")
        # Do NOT start — tick stays 0, no reconstruction in publish()

        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await core.events.subscribe("probe", handler)
        await core.events.publish(Event("probe", {}, source="manual_source"))

        # Inline dispatch (no gate) — a few yields suffice
        for _ in range(5):
            await asyncio.sleep(0)

        assert len(received) == 1
        assert received[0].source == "manual_source"

    @pytest.mark.asyncio
    async def test_source_and_name_are_independent(self, core: Core) -> None:
        """event.name is the topic; event.source is metadata — not the same string."""
        await core.start()
        emitter = Plugin(name="emitter")
        await core.register_plugin(emitter)

        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await core.events.subscribe("ping", handler)
        await emitter.emit("ping", {})
        await wait_until(lambda: len(received) == 1)

        evt = received[0]
        assert evt.name == "ping"
        assert evt.source == "emitter"
        assert evt.name != evt.source  # topic and emitter are distinct

        await core.stop()


# ---------------------------------------------------------------------------
# Explicit dotted names + wildcard matching still work
# ---------------------------------------------------------------------------


class TestWildcardAndDottedNames:
    """Wildcard subscriptions and explicit dotted names work unchanged.

    The removal of auto-prefixing does NOT touch the subscription matcher;
    authors who want plugin-namespaced topics write them explicitly.
    """

    @pytest.mark.asyncio
    async def test_explicit_dotted_name_matches_wildcard_subscriber(self, core: Core) -> None:
        """emit("sensor.reading") with explicit dot matches @event("sensor.*") subscriber."""
        await core.start()
        sensor = Plugin(name="sensor")
        await core.register_plugin(sensor)

        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await core.events.subscribe("sensor.*", handler)
        await sensor.emit("sensor.reading", {"v": 3.14})
        await wait_until(lambda: len(received) == 1)

        # The event name is the explicitly-dotted string given to emit()
        assert received[0].name == "sensor.reading"

        await core.stop()

    @pytest.mark.asyncio
    async def test_flat_name_does_not_match_old_wildcard(self, core: Core) -> None:
        """emit("reading") (flat) is NOT caught by "sensor.*" pattern.

        "sensor.*" requires a literal 'sensor.' prefix in the event name.
        A plugin emitting flat names doesn't produce those names anymore.
        """
        await core.start()
        sensor = Plugin(name="sensor")
        await core.register_plugin(sensor)

        caught_by_wildcard: list[Event] = []

        async def on_wildcard(event: Event) -> None:
            caught_by_wildcard.append(event)

        flat_received: list[Event] = []

        async def on_flat(event: Event) -> None:
            flat_received.append(event)

        await core.events.subscribe("sensor.*", on_wildcard)
        await core.events.subscribe("reading", on_flat)
        await sensor.emit("reading", {})
        await wait_until(lambda: len(flat_received) == 1)

        assert caught_by_wildcard == [], (
            f"'sensor.*' must not match flat 'reading'; caught {len(caught_by_wildcard)} event(s)"
        )
        assert len(flat_received) == 1

        await core.stop()


# ---------------------------------------------------------------------------
# Deferred emit (at_tick=N) also sets source
# ---------------------------------------------------------------------------


class TestDeferredEmitSource:
    """emit(name, data, at_tick=N) fires at the right tick.

    Uses fake-time driving for deterministic tick control — no asyncio.sleep
    synchronization.
    """

    @pytest.mark.asyncio
    async def test_deferred_emit_fires_at_correct_tick(self) -> None:
        """emit(at_tick=T+2) delivers the event at exactly the second driven boundary.

        In the no-gate model, register_plugin completes directly. The scheduled
        task fires at T+2; the subsequent publish() dispatch is fire-and-forget,
        so we wait on a signal rather than asserting synchronously.
        """
        from tests.fake_time import FakeTime, install_fake_time, run_ticks

        core = Core(tick_rate=100, hook_precaching="disabled")
        fake = FakeTime()
        install_fake_time(core, fake)
        clock = core._tick_clock

        plugin = Plugin(name="timer_plugin")
        received: list[Event] = []
        signal = asyncio.Event()

        async def handler(event: Event) -> None:
            received.append(event)
            signal.set()

        await core.events.subscribe("alarm", handler)
        await core.start()

        # No gate — register_plugin completes directly.
        await core.register_plugin(plugin)

        target_tick = clock.tick + 2
        await plugin.emit("alarm", {"reason": "test"}, at_tick=target_tick)

        # Drive 1 tick: alarm must NOT fire yet.
        # Check BEFORE extra yields — don't let the clock pre-advance.
        await run_ticks(fake, clock, 1)
        assert received == [], "Alarm fired one tick too early"

        # Drive the second tick: scheduled task fires and publishes.
        await run_ticks(fake, clock, 1)
        await asyncio.wait_for(signal.wait(), timeout=2.0)

        assert len(received) == 1
        assert received[0].name == "alarm"

        await core.stop()

    @pytest.mark.asyncio
    async def test_deferred_emit_sets_source(self) -> None:
        """emit(at_tick=T+2) fires with event.source == emitter plugin name."""
        from tests.fake_time import FakeTime, install_fake_time, run_ticks

        core = Core(tick_rate=100, hook_precaching="disabled")
        fake = FakeTime()
        install_fake_time(core, fake)
        clock = core._tick_clock

        plugin = Plugin(name="timer_plugin")
        received: list[Event] = []
        signal = asyncio.Event()

        async def handler(event: Event) -> None:
            received.append(event)
            signal.set()

        await core.events.subscribe("alarm", handler)
        await core.start()

        # No gate — register_plugin completes directly.
        await core.register_plugin(plugin)

        target_tick = clock.tick + 2
        await plugin.emit("alarm", {"reason": "test"}, at_tick=target_tick)

        # Check BEFORE extra yields — don't let the clock pre-advance.
        await run_ticks(fake, clock, 1)
        assert received == [], "Alarm fired early"

        await run_ticks(fake, clock, 1)
        await asyncio.wait_for(signal.wait(), timeout=2.0)

        assert len(received) == 1
        assert received[0].source == "timer_plugin"

        await core.stop()
