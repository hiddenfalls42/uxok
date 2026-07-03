"""Tests for the Plugin base class: lifecycle, convenience methods, and background tasks."""

from __future__ import annotations

import asyncio

import pytest

from uxok import Core, Plugin
from uxok.plugin._decorators import hook
from uxok.protocols import Event


class TestPluginLifecycle:
    @pytest.mark.asyncio
    async def test_start_idempotent(self, started_core: Core):
        p = Plugin(name="idem_test")
        await started_core.register_plugin(p)
        await p.start()  # Already started — should be no-op

    @pytest.mark.asyncio
    async def test_stop_before_start_is_noop(self, clean_core: Core):
        p = Plugin(name="noop_stop")
        await p.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_start_after_shutdown_raises(self, clean_core: Core):
        p = Plugin(name="shutdown_test")
        p._initialized = False
        p._shutdown = True
        with pytest.raises(RuntimeError, match="Cannot start plugin after shutdown"):
            await p.start()

    @pytest.mark.asyncio
    async def test_stop_error_in_on_stop_handled(self, started_core: Core):
        class CrashPlugin(Plugin):
            def __init__(self):
                super().__init__(name="crash_plugin")

            async def on_stop(self):
                raise RuntimeError("crash on stop")

        p = CrashPlugin()
        await started_core.register_plugin(p)
        # Should not raise
        await started_core.unregister_plugin(p.metadata.name)


class TestPluginHookRegistration:
    @pytest.mark.asyncio
    async def test_start_registers_decorated_hooks(self, started_core: Core):
        class HookPlugin(Plugin):
            def __init__(self):
                super().__init__(name="hook_plugin")

            @hook("data.process", priority=7)
            async def process(self, data):
                return data

        p = HookPlugin()
        await started_core.register_plugin(p)

        hooks = await started_core.hooks.get_hooks("data.process")
        assert len(hooks) == 1
        priority, hook_obj = hooks[0]
        assert priority == 7
        assert hook_obj.plugin_id == str(p.metadata.id)


class TestPluginConvenience:
    @pytest.mark.asyncio
    async def test_config_accessor(self, clean_core: Core):
        p = Plugin(name="config_test")
        p._attach_core(clean_core)
        # CoreConfig fields are not accessible via plugin.config() — they are
        # kernel-internal; plugin config is scoped to the plugin namespace only.
        assert p.config("tick_rate") is None
        assert p.config("tick_rate", 999) == 999
        assert p.config("nonexistent", "default") == "default"

    @pytest.mark.asyncio
    async def test_emit(self, started_core: Core):
        p = Plugin(name="emitter")
        await started_core.register_plugin(p)

        received = []

        async def handler(event):
            received.append(event.name)

        await started_core.events.subscribe("test_event", handler)
        await p.emit("test_event", {"data": True})
        await asyncio.sleep(0.005)
        assert "test_event" in received

    @pytest.mark.asyncio
    async def test_get_capability(self, started_core: Core):
        provider = Plugin(name="provider", provides={"storage"})
        consumer = Plugin(name="consumer", requires={"storage"})
        await started_core.register_plugin(provider)
        await started_core.register_plugin(consumer)
        assert await consumer.get_capability("storage") is provider

    @pytest.mark.asyncio
    async def test_create_background_task(self, clean_core: Core):
        p = Plugin(name="task_test")

        completed = False

        async def bg_work():
            nonlocal completed
            completed = True

        task = await p.create_background_task(bg_work(), name="test_task")
        await task
        assert completed is True

    # ------------------------------------------------------------------
    # Demand-driven emission — has_subscribers + emit short-circuit
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_has_subscribers_false_with_no_subscriber(self, started_core: Core) -> None:
        """has_subscribers returns False when nobody is subscribed to the topic."""
        p = Plugin(name="demand_plugin")
        await started_core.register_plugin(p)

        assert p.has_subscribers("unheard.topic") is False

    @pytest.mark.asyncio
    async def test_has_subscribers_true_after_subscribe(self, started_core: Core) -> None:
        """has_subscribers returns True once a handler is subscribed."""
        p = Plugin(name="demand_plugin2")
        await started_core.register_plugin(p)

        await started_core.events.subscribe("heard.topic", lambda e: None)
        assert p.has_subscribers("heard.topic") is True

    @pytest.mark.asyncio
    async def test_has_subscribers_false_when_muted(self, started_core: Core) -> None:
        """has_subscribers returns False when the topic is muted, even with a subscriber."""
        p = Plugin(name="demand_plugin3")
        await started_core.register_plugin(p)

        await started_core.events.subscribe("noisy.topic", lambda e: None)
        assert p.has_subscribers("noisy.topic") is True  # baseline

        started_core.events.mute("noisy.*")
        assert p.has_subscribers("noisy.topic") is False

    @pytest.mark.asyncio
    async def test_has_subscribers_true_after_unmute(self, started_core: Core) -> None:
        """has_subscribers returns True again after unmuting a previously muted topic."""
        p = Plugin(name="demand_plugin4")
        await started_core.register_plugin(p)

        await started_core.events.subscribe("toggle.topic", lambda e: None)
        started_core.events.mute("toggle.*")
        started_core.events.unmute("toggle.*")

        assert p.has_subscribers("toggle.topic") is True

    @pytest.mark.asyncio
    async def test_emit_skips_when_no_subscriber(self, started_core: Core) -> None:
        """emit() completes without error and invokes no handler when nobody is subscribed."""
        p = Plugin(name="silent_emitter")
        await started_core.register_plugin(p)

        spy_called = False

        async def spy(e: Event) -> None:
            nonlocal spy_called
            spy_called = True

        # Spy is on a DIFFERENT topic — proves the emit for the silent topic
        # has no side effects, while still giving us a settled-dispatch reference.
        await started_core.events.subscribe("other.topic", spy)

        # Must not raise; nobody is listening to "nobody.listening"
        await p.emit("nobody.listening", {"data": 1})
        # Yield to confirm nothing dispatched for the silent topic.
        await asyncio.sleep(0)
        assert spy_called is False

    @pytest.mark.asyncio
    async def test_emit_skips_when_muted_handler_not_called(self, started_core: Core) -> None:
        """emit() does not invoke the handler when the topic is muted."""
        p = Plugin(name="muted_emitter")
        await started_core.register_plugin(p)

        received: list[Event] = []

        async def spy(e: Event) -> None:
            received.append(e)

        await started_core.events.subscribe("muted.channel", spy)
        started_core.events.mute("muted.*")

        await p.emit("muted.channel", {"val": 1})
        await asyncio.sleep(0.01)  # give fire-and-forget tasks a chance to run

        assert received == []

    @pytest.mark.asyncio
    async def test_emit_delivers_after_unmute(self, started_core: Core) -> None:
        """emit() delivers to the handler once the topic is unmuted."""
        p = Plugin(name="unmute_emitter")
        await started_core.register_plugin(p)

        received: list[Event] = []
        signal = asyncio.Event()

        async def spy(e: Event) -> None:
            received.append(e)
            signal.set()

        await started_core.events.subscribe("toggle.channel", spy)
        started_core.events.mute("toggle.*")

        await p.emit("toggle.channel", {"round": 1})
        await asyncio.sleep(0.01)
        assert received == []  # still muted

        started_core.events.unmute("toggle.*")
        await p.emit("toggle.channel", {"round": 2})
        await asyncio.wait_for(signal.wait(), timeout=1.0)

        assert len(received) == 1
        assert received[0].data == {"round": 2}

    @pytest.mark.asyncio
    async def test_emit_subscribed_delivers_end_to_end(self, started_core: Core) -> None:
        """Regression: a normal subscribed emit still delivers the event end-to-end."""
        p = Plugin(name="normal_emitter")
        await started_core.register_plugin(p)

        received: list[Event] = []
        signal = asyncio.Event()

        async def handler(e: Event) -> None:
            received.append(e)
            signal.set()

        await started_core.events.subscribe("live.delivery", handler)
        await p.emit("live.delivery", {"ok": True})
        await asyncio.wait_for(signal.wait(), timeout=1.0)

        assert len(received) == 1
        assert received[0].name == "live.delivery"
        assert received[0].data == {"ok": True}
        assert received[0].source == "normal_emitter"


class TestPluginMetadataHooksEvents:
    """Test Plugin initialization with hooks_consumed and events_published metadata."""

    @pytest.mark.asyncio
    async def test_plugin_with_hooks_consumed_and_events_published(self, clean_core: Core):
        """Test Plugin accepts hooks_consumed and events_published kwargs."""

        class DeclarativeMetadataPlugin(Plugin):
            def __init__(self):
                super().__init__(
                    name="test_plugin",
                    hooks_consumed={"data.validate", "data.transform"},
                    events_published={"data.processed", "data.error"},
                )

        plugin = DeclarativeMetadataPlugin()

        assert plugin.metadata.hooks_consumed == frozenset(["data.validate", "data.transform"])
        assert plugin.metadata.events_published == frozenset(["data.processed", "data.error"])

    @pytest.mark.asyncio
    async def test_plugin_defaults_for_hooks_events(self, clean_core: Core):
        """Test plugins without hooks_consumed/events_published use defaults."""

        class SimplePlugin(Plugin):
            def __init__(self):
                super().__init__(name="simple")

        plugin = SimplePlugin()

        assert plugin.metadata.hooks_consumed == frozenset()
        assert plugin.metadata.events_published == frozenset()

    @pytest.mark.asyncio
    async def test_plugin_with_hooks_consumed_events_published_in_proxy(self, started_core: Core):
        """Test that hooks_consumed and events_published are properly exposed in proxy."""

        class DataProcessor(Plugin):
            def __init__(self):
                super().__init__(
                    name="data_processor",
                    version="1.0.0",
                    provides={"processing"},
                    hooks_consumed={"data.validate", "data.transform"},
                    events_published={"data.processed", "data.error"},
                )

        plugin = DataProcessor()
        await started_core.register_plugin(plugin)

        plugins = await started_core.list()
        proxy = plugins[0]

        assert set(proxy.hooks_consumed) == {"data.validate", "data.transform"}
        assert set(proxy.events_published) == {"data.processed", "data.error"}

    @pytest.mark.asyncio
    async def test_hook_consumes_filter_with_declarative_metadata(self, started_core: Core):
        """Test .hook.consumes() returns plugins with hooks_consumed metadata."""

        class HookConsumer(Plugin):
            def __init__(self):
                super().__init__(name="consumer", hooks_consumed={"data.validate"})

        class HookProvider(Plugin):
            def __init__(self):
                super().__init__(name="provider")

        consumer = HookConsumer()
        provider = HookProvider()

        await started_core.register_plugin(consumer)
        await started_core.register_plugin(provider)

        plugins = await started_core.list()

        # Filter by hook consumed
        consumers = plugins.hook.consumes("data.validate")
        assert len(consumers) == 1
        assert consumers[0].name == "consumer"

    @pytest.mark.asyncio
    async def test_event_provides_filter_with_declarative_metadata(self, started_core: Core):
        """Test .event.provides() returns plugins with events_published metadata."""

        class EventPublisher(Plugin):
            def __init__(self):
                super().__init__(name="publisher", events_published={"user.login"})

        class EventSubscriber(Plugin):
            def __init__(self):
                super().__init__(name="subscriber")

        publisher = EventPublisher()
        subscriber = EventSubscriber()

        await started_core.register_plugin(publisher)
        await started_core.register_plugin(subscriber)

        plugins = await started_core.list()

        # Filter by event published
        publishers = plugins.event.provides("user.login")
        assert len(publishers) == 1
        assert publishers[0].name == "publisher"
