"""Tests for dynamic event subscription and hook registration.

Covers the new public primitives:
  - Plugin.subscribe(event_pattern, handler)     — runtime event subscription
  - Plugin.register_hook(hook_name, handler, *, priority=0) — runtime hook registration
  - HookSystem.register(name, callback, *, priority, plugin_id) — primitives-level
  - Decorator regression: @event / @hook still work after desugaring

Cleanup semantics (subscribe/register_hook cleaned up on unregister) and
priority ordering are also verified.
"""

from __future__ import annotations

import asyncio

import pytest

from uxok import Core, Plugin
from uxok.protocols import Event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _drain(n: int = 5) -> None:
    """Yield control n times to let queued tick-gate work drain."""
    for _ in range(n):
        await asyncio.sleep(0)


# A plugin that subscribes to a *runtime-computed* event name in on_start.
class RuntimeSubscriber(Plugin):
    """Subscribes to an event whose name is computed from constructor args."""

    def __init__(self, *, event_name: str, name: str = "runtime_subscriber", **kw):
        super().__init__(name=name, **kw)
        self._target_event = event_name
        self.received: list[Event] = []

    async def on_start(self) -> None:
        await self.subscribe(self._target_event, self._on_event)

    async def _on_event(self, event: Event) -> None:
        self.received.append(event)


# A plugin that subscribes to a wildcard pattern in on_start.
class WildcardSubscriber(Plugin):
    def __init__(self, **kw):
        super().__init__(name="wildcard_subscriber", **kw)
        self.received: list[Event] = []

    async def on_start(self) -> None:
        await self.subscribe("sensor.*", self._on_sensor)

    async def _on_sensor(self, event: Event) -> None:
        self.received.append(event)


# A plugin that registers a hook at runtime in on_start.
class RuntimeHookPlugin(Plugin):
    def __init__(self, *, hook_name: str, priority: int = 0, **kw):
        super().__init__(name="runtime_hook_plugin", **kw)
        self._hook_name = hook_name
        self._priority = priority
        self.call_log: list[str] = []

    async def on_start(self) -> None:
        await self.register_hook(self._hook_name, self._handler, priority=self._priority)

    async def _handler(self, **kwargs) -> str:
        self.call_log.append("fired")
        return "result"


# ---------------------------------------------------------------------------
# Plugin.subscribe — runtime event name
# ---------------------------------------------------------------------------


class TestPluginSubscribeRuntime:
    @pytest.mark.asyncio
    async def test_runtime_subscribe_receives_emitted_event(self, clean_core: Core) -> None:
        """A plugin subscribing in on_start to a runtime-computed name receives events.

        This is the case the @event decorator cannot express: the pattern is
        determined at instantiation time from constructor data, not a fixed literal.
        """
        core = clean_core
        await core.start()

        subscriber = RuntimeSubscriber(event_name="flow.trigger.abc")
        await core.register_plugin(subscriber)

        emitter = Plugin(name="emitter")
        await core.register_plugin(emitter)

        await core.events.publish(Event("flow.trigger.abc", {"payload": 1}))
        await asyncio.sleep(0.05)

        assert len(subscriber.received) == 1
        assert subscriber.received[0].data["payload"] == 1

        await core.stop()

    @pytest.mark.asyncio
    async def test_runtime_subscribe_different_names_are_independent(
        self, clean_core: Core
    ) -> None:
        """Two subscribers with different runtime names only receive their own events."""
        core = clean_core
        await core.start()

        sub_a = RuntimeSubscriber(event_name="ns.a", name="subscriber_a")
        sub_b = RuntimeSubscriber(event_name="ns.b", name="subscriber_b")
        await core.register_plugin(sub_a)
        await core.register_plugin(sub_b)

        await core.events.publish(Event("ns.a", {}))
        await asyncio.sleep(0.05)

        assert len(sub_a.received) == 1
        assert len(sub_b.received) == 0

        await core.stop()


# ---------------------------------------------------------------------------
# Plugin.subscribe — wildcard pattern
# ---------------------------------------------------------------------------


class TestPluginSubscribeWildcard:
    @pytest.mark.asyncio
    async def test_wildcard_subscribe_matches_prefix(self, clean_core: Core) -> None:
        """subscribe('sensor.*') receives events matching the glob pattern."""
        core = clean_core
        await core.start()

        sub = WildcardSubscriber()
        await core.register_plugin(sub)

        await core.events.publish(Event("sensor.reading", {"v": 42}))
        await asyncio.sleep(0.05)

        assert len(sub.received) == 1
        assert sub.received[0].name == "sensor.reading"

        await core.stop()

    @pytest.mark.asyncio
    async def test_wildcard_does_not_match_unrelated_prefix(self, clean_core: Core) -> None:
        """subscribe('sensor.*') does not fire for unrelated event names."""
        core = clean_core
        await core.start()

        sub = WildcardSubscriber()
        await core.register_plugin(sub)

        await core.events.publish(Event("actuator.command", {}))
        await asyncio.sleep(0.05)

        assert sub.received == []

        await core.stop()


# ---------------------------------------------------------------------------
# Plugin.subscribe — cleanup on unregister (no zombie)
# ---------------------------------------------------------------------------


class TestPluginSubscribeCleanup:
    @pytest.mark.asyncio
    async def test_unregistered_plugin_no_longer_receives_events(self, clean_core: Core) -> None:
        """After unregistering the subscribing plugin, emitting the event does not
        invoke its handler — no zombie subscription."""
        core = clean_core
        await core.start()

        subscriber = RuntimeSubscriber(event_name="ping.event")
        await core.register_plugin(subscriber)

        # Confirm it works before unregistration.
        await core.events.publish(Event("ping.event", {}))
        await asyncio.sleep(0.05)
        assert len(subscriber.received) == 1

        await core.unregister_plugin(subscriber.metadata.id)

        # Emit again — handler must NOT fire.
        await core.events.publish(Event("ping.event", {}))
        await asyncio.sleep(0.05)
        assert len(subscriber.received) == 1, (
            "unregistered plugin's subscribe handler fired after unregistration"
        )

        await core.stop()


# ---------------------------------------------------------------------------
# Plugin.register_hook — runtime
# ---------------------------------------------------------------------------


class TestPluginRegisterHookRuntime:
    @pytest.mark.asyncio
    async def test_runtime_hook_executes_via_core_hooks(self, clean_core: Core) -> None:
        """A hook registered at runtime via register_hook() fires when executed."""
        core = clean_core
        await core.start()

        plugin = RuntimeHookPlugin(hook_name="work.process")
        await core.register_plugin(plugin)

        results = await core.hooks.execute("work.process")

        assert len(results) == 1
        assert results[0] == "result"
        assert plugin.call_log == ["fired"]

        await core.stop()

    @pytest.mark.asyncio
    async def test_runtime_hook_executes_via_plugin_hook_method(self, clean_core: Core) -> None:
        """Plugin.hook() (the convenience method) reaches a dynamically-registered handler."""
        core = clean_core
        await core.start()

        plugin = RuntimeHookPlugin(hook_name="signal.send")
        await core.register_plugin(plugin)

        caller = Plugin(name="caller")
        await core.register_plugin(caller)

        # Use the Plugin.hook() convenience method.
        result = await caller.hook("signal.send")

        assert result == ["result"]
        assert plugin.call_log == ["fired"]

        await core.stop()


# ---------------------------------------------------------------------------
# Plugin.register_hook — priority ordering
# ---------------------------------------------------------------------------


class TestPluginRegisterHookPriority:
    @pytest.mark.asyncio
    async def test_higher_priority_handler_runs_first(self, clean_core: Core) -> None:
        """Two handlers registered for the same hook fire in priority order (higher first).

        _sort_hooks in _system.py sorts descending by priority, so the higher
        integer fires first.
        """
        core = clean_core
        await core.start()

        order: list[str] = []

        class HighPriorityPlugin(Plugin):
            async def on_start(self) -> None:
                await self.register_hook("ordered.hook", self._handle, priority=10)

            async def _handle(self) -> None:
                order.append("high")

        class LowPriorityPlugin(Plugin):
            async def on_start(self) -> None:
                await self.register_hook("ordered.hook", self._handle, priority=1)

            async def _handle(self) -> None:
                order.append("low")

        high = HighPriorityPlugin(name="high_prio")
        low = LowPriorityPlugin(name="low_prio")
        await core.register_plugin(high)
        await core.register_plugin(low)

        await core.hooks.execute("ordered.hook")

        assert order == ["high", "low"], (
            f"expected ['high', 'low'] but got {order!r} — priority ordering broken"
        )

        await core.stop()


# ---------------------------------------------------------------------------
# Plugin.register_hook — cleanup on unregister
# ---------------------------------------------------------------------------


class TestPluginRegisterHookCleanup:
    @pytest.mark.asyncio
    async def test_unregistered_plugin_hook_no_longer_executes(self, clean_core: Core) -> None:
        """After unregistering the plugin, its dynamic hook no longer fires."""
        core = clean_core
        await core.start()

        plugin = RuntimeHookPlugin(hook_name="cleanup.hook")
        await core.register_plugin(plugin)

        # Confirm it fires.
        r1 = await core.hooks.execute("cleanup.hook")
        assert r1 == ["result"]

        await core.unregister_plugin(plugin.metadata.id)

        # After unregistration the hook list for this name must be empty.
        r2 = await core.hooks.execute("cleanup.hook")
        assert r2 == [], "hook still executing after plugin was unregistered — cleanup failed"

        await core.stop()


# ---------------------------------------------------------------------------
# HookSystem.register — primitives-based direct call
# ---------------------------------------------------------------------------


class TestHookSystemRegisterDirect:
    @pytest.mark.asyncio
    async def test_direct_register_executes_correctly(self, clean_core: Core) -> None:
        """core.hooks.register(name, fn, priority, plugin_id) registers and executes."""
        core = clean_core
        await core.start()

        calls: list[str] = []

        async def handler() -> str:
            calls.append("hit")
            return "direct"

        await core.hooks.register("direct.hook", handler, priority=5, plugin_id="test_plugin")
        result = await core.hooks.execute("direct.hook")

        assert result == ["direct"]
        assert calls == ["hit"]

        await core.stop()

    @pytest.mark.asyncio
    async def test_direct_register_invalid_name_raises(self, clean_core: Core) -> None:
        """core.hooks.register raises ValueError for an invalid hook name."""
        core = clean_core
        await core.start()

        with pytest.raises(ValueError, match="Invalid hook name"):
            await core.hooks.register("bad name!", lambda: None)

        await core.stop()

    @pytest.mark.asyncio
    async def test_direct_register_non_callable_raises(self, clean_core: Core) -> None:
        """core.hooks.register raises ValueError for a non-callable callback."""
        core = clean_core
        await core.start()

        with pytest.raises(ValueError, match="handler must be callable"):
            await core.hooks.register("ok.name", "not-callable")  # type: ignore[arg-type]

        await core.stop()


# ---------------------------------------------------------------------------
# Decorator regression — static @event / @hook still work after desugaring
# ---------------------------------------------------------------------------


class TestDecoratorRegression:
    @pytest.mark.asyncio
    async def test_event_decorator_still_receives_events(self, clean_core: Core) -> None:
        """A @event-decorated handler on a plugin still fires after the desugaring
        change (decorators now desugar to Plugin.subscribe internally)."""
        from uxok import event

        received: list[Event] = []

        class DecoratedPlugin(Plugin):
            def __init__(self, **kw):
                super().__init__(name="decorated_event", **kw)

            @event("probe.signal")
            async def on_probe(self, ev: Event) -> None:
                received.append(ev)

        core = clean_core
        await core.start()

        plugin = DecoratedPlugin()
        await core.register_plugin(plugin)

        await core.events.publish(Event("probe.signal", {"n": 7}))
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0].data["n"] == 7

        await core.stop()

    @pytest.mark.asyncio
    async def test_hook_decorator_still_executes(self, clean_core: Core) -> None:
        """A @hook-decorated method on a plugin still fires after the desugaring
        change (decorators now desugar to Plugin.register_hook internally)."""
        from uxok import hook

        results_log: list[str] = []

        class DecoratedHookPlugin(Plugin):
            def __init__(self, **kw):
                super().__init__(name="decorated_hook", **kw)

            @hook("probe.hook")
            async def on_hook(self) -> str:
                results_log.append("fired")
                return "hook-result"

        core = clean_core
        await core.start()

        plugin = DecoratedHookPlugin()
        await core.register_plugin(plugin)

        results = await core.hooks.execute("probe.hook")

        assert results == ["hook-result"]
        assert results_log == ["fired"]

        await core.stop()


# ---------------------------------------------------------------------------
# Hot-reload cleanup — dynamically-subscribed handler gone after reload
# ---------------------------------------------------------------------------


_DYNAMIC_SUBSCRIBER_V1 = """
class DynamicSubscriber(Plugin):
    def __init__(self, **kw):
        super().__init__(name="dynamic_sub", **kw)
        self.received = []

    async def on_start(self):
        await self.subscribe("dyn.signal", self._on_signal)

    async def _on_signal(self, event):
        self.received.append(("v1", event.data.get("n")))
"""
_DYNAMIC_SUBSCRIBER_V2 = _DYNAMIC_SUBSCRIBER_V1.replace('"v1"', '"v2"')

_DYNAMIC_HOOK_V1 = """
class DynamicHooker(Plugin):
    def __init__(self, **kw):
        super().__init__(name="dynamic_hooker", **kw)

    async def on_start(self):
        await self.register_hook("dyn.hook", self._handle)

    async def _handle(self, **kw):
        return "v1-marker"
"""
_DYNAMIC_HOOK_V2 = _DYNAMIC_HOOK_V1.replace('"v1-marker"', '"v2-marker"')

# Closure handler (no __self__) — only the explicit owner binding can attribute it
# to the instance for hot-reload drain. Exercises the owner-channel fix directly.
_DYNAMIC_CLOSURE_HOOK_V1 = """
class ClosureHooker(Plugin):
    def __init__(self, **kw):
        super().__init__(name="closure_hooker", **kw)

    async def on_start(self):
        marker = "v1-closure"
        async def _handler(**kw):
            return marker
        await self.register_hook("dyn.closure_hook", _handler)
"""
_DYNAMIC_CLOSURE_HOOK_V2 = _DYNAMIC_CLOSURE_HOOK_V1.replace('"v1-closure"', '"v2-closure"')


class TestHotReloadCleanup:
    @pytest.mark.asyncio
    async def test_dynamic_subscribe_cleaned_up_after_reload(self, started_core: Core) -> None:
        """After reloading a plugin that subscribed dynamically in on_start,
        the OLD instance's handler no longer fires — no zombie double-fire.

        Uses the load_plugin hot-reload path from test_hot_reload.py pattern.
        """
        core = started_core

        await core.load_plugin(_DYNAMIC_SUBSCRIBER_V1)
        v1 = await core.get_plugin("dynamic_sub")

        await core.events.publish(Event("dyn.signal", {"n": 0}))
        await asyncio.sleep(0.05)
        assert v1.received == [("v1", 0)]

        await core.load_plugin(_DYNAMIC_SUBSCRIBER_V2)
        v2 = await core.get_plugin("dynamic_sub")
        assert v2 is not v1

        await core.events.publish(Event("dyn.signal", {"n": 1}))
        await asyncio.sleep(0.05)

        # v1 handler must NOT have received the second event.
        assert v1.received == [("v1", 0)], (
            "old instance's dynamic subscribe handler still firing after reload — zombie"
        )
        # v2 handler received exactly one event.
        assert v2.received == [("v2", 1)]

    @pytest.mark.asyncio
    async def test_dynamic_hook_cleaned_up_after_reload(self, started_core: Core) -> None:
        """After reloading a plugin that registered a hook dynamically in on_start,
        only the new instance's hook fires — no double-execution."""
        core = started_core

        await core.load_plugin(_DYNAMIC_HOOK_V1)
        r1 = await core.hooks.execute("dyn.hook")
        assert r1 == ["v1-marker"]

        await core.load_plugin(_DYNAMIC_HOOK_V2)
        r2 = await core.hooks.execute("dyn.hook")

        # Exactly one execution, v2 result only.
        assert len(r2) == 1, (
            f"expected 1 result after reload, got {len(r2)} — zombie hook still registered"
        )
        assert r2[0] == "v2-marker"

    @pytest.mark.asyncio
    async def test_dynamic_closure_hook_cleaned_up_after_reload(self, started_core: Core) -> None:
        """A CLOSURE handler registered via register_hook is drained on hot-reload.

        Closures have no bound-method ``__self__``, so only the explicit ``owner``
        binding can attribute them to the old instance. Before the owner channel
        existed this leaked and double-fired; this guards the fix.
        """
        core = started_core

        await core.load_plugin(_DYNAMIC_CLOSURE_HOOK_V1)
        r1 = await core.hooks.execute("dyn.closure_hook")
        assert r1 == ["v1-closure"]

        await core.load_plugin(_DYNAMIC_CLOSURE_HOOK_V2)
        r2 = await core.hooks.execute("dyn.closure_hook")

        # The old instance's closure hook must be gone — exactly one (v2) result.
        assert r2 == ["v2-closure"], (
            f"expected only the v2 closure hook after reload, got {r2} — "
            "old closure hook leaked (owner-channel cleanup failed)"
        )
