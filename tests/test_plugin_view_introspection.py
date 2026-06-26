"""Integration tests for PluginView introspection: live members and capability info.

Covers:
- EAFP held-ref: view fetched before unregister; stale actions raise StalePluginError
- Live status: status/ready reflect live state after post-fetch stop/unregister
- view.methods(): returns own public methods with signature dicts; excludes Plugin base methods
- collection.capability.info(): returns CapabilityInfo for typed/untyped capabilities;
  None for unknown names; None via hook/event filter
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pytest

from uxok import Plugin, StalePluginError
from uxok.registry import CapabilityInfo

# ---------------------------------------------------------------------------
# Shared protocol and plugin definitions
# ---------------------------------------------------------------------------


@runtime_checkable
class Greeter(Protocol):
    """A typed greeting capability."""

    async def hello(self, name: str) -> str: ...
    async def goodbye(self, name: str) -> str: ...


class GreeterPlugin(Plugin):
    """Plugin that provides the typed Greeter capability."""

    def __init__(self):
        super().__init__(name="greeter", provides={Greeter})

    async def hello(self, name: str) -> str:
        return f"Hello, {name}!"

    async def goodbye(self, name: str) -> str:
        return f"Goodbye, {name}!"

    def greet_sync(self, name: str) -> str:
        """A sync method also owned by this plugin."""
        return f"Hey {name}"


class UntypedPlugin(Plugin):
    """Plugin that provides a string-only (untyped) capability."""

    def __init__(self):
        super().__init__(name="untyped", provides={"storage"})

    async def read(self, path: str) -> bytes:
        return b""

    def write(self, path: str, data: bytes) -> None:
        pass


class MethodsPlugin(Plugin):
    """Plugin with diverse own methods, used to test view.methods()."""

    def __init__(self):
        super().__init__(name="methods_plugin")

    def public_sync(self, x: int, y: int = 0) -> int:
        """A public sync method."""
        return x + y

    async def public_async(self, msg: str) -> str:
        """A public async method."""
        return msg.upper()

    def _private_method(self) -> None:
        """Should be excluded from view.methods() output."""


# ---------------------------------------------------------------------------
# EAFP held-ref tests
# ---------------------------------------------------------------------------


class TestEafpHeldRef:
    """Held PluginView live reads raise StalePluginError once the plugin is unregistered.

    The registry (membership), not object liveness, is the source of truth for the
    live reads (``uptime``/``methods``). These tests deliberately keep a STRONG
    reference to the plugin instance across unregister — so the object is NOT
    garbage-collected — and assert the read still raises. This proves the staleness
    gate is registry-membership, not GC of the weakref: a held view must never read
    off a torn-down-but-still-referenced plugin.
    """

    @pytest.mark.asyncio
    async def test_uptime_on_stale_view_raises_stale_plugin_error(self, clean_core):
        """uptime() raises StalePluginError after unregister, even with a live object ref."""
        plugin = Plugin(name="ephemeral")  # strong ref held
        await clean_core.register_plugin(plugin)
        view = (await clean_core.list()).by_name("ephemeral")

        await clean_core.unregister_plugin("ephemeral")

        with pytest.raises(StalePluginError):
            await view.uptime()

    @pytest.mark.asyncio
    async def test_methods_on_stale_view_raises_stale_plugin_error(self, clean_core):
        """methods() raises StalePluginError after unregister, even with a live object ref."""
        plugin = Plugin(name="fleeting")  # strong ref held
        await clean_core.register_plugin(plugin)
        view = (await clean_core.list()).by_name("fleeting")

        await clean_core.unregister_plugin("fleeting")

        with pytest.raises(StalePluginError):
            await view.methods()

    @pytest.mark.asyncio
    async def test_status_returns_stopped_when_plugin_unregistered(self, clean_core):
        """status is 'stopped' after unregister (via live _shutdown flag), not a stale snapshot."""
        await clean_core.register_plugin(Plugin(name="transient"))
        # Keep a reference to the plugin so the weakref stays alive — the _shutdown
        # flag path is enough to exercise this without requiring a GC cycle.
        view = (await clean_core.list()).by_name("transient")

        assert view.status == "active"

        await clean_core.unregister_plugin("transient")

        # status resolves live from the _shutdown flag — returns "stopped" even
        # though the plugin object is still in memory
        assert view.status == "stopped"

    @pytest.mark.asyncio
    async def test_ready_is_false_after_unregister(self, clean_core):
        """ready is False once the plugin has been stopped/unregistered."""
        await clean_core.register_plugin(Plugin(name="short_lived"))
        view = (await clean_core.list()).by_name("short_lived")

        assert view.ready is True

        await clean_core.unregister_plugin("short_lived")

        assert view.ready is False


# ---------------------------------------------------------------------------
# Live status tests
# ---------------------------------------------------------------------------


class TestLiveStatus:
    """status/ready reflect the live instance, not frozen snapshot values."""

    @pytest.mark.asyncio
    async def test_status_tracks_lifecycle_active(self, clean_core):
        """A view fetched post-start reports 'active'."""
        await clean_core.register_plugin(Plugin(name="tracked"))
        view = (await clean_core.list()).by_name("tracked")

        assert view.status == "active"
        assert view.ready is True

    @pytest.mark.asyncio
    async def test_status_changes_to_stopped_after_unregister(self, clean_core):
        """status changes from 'active' to 'stopped' after unregister without refetch."""
        await clean_core.register_plugin(Plugin(name="mutable_state"))
        view = (await clean_core.list()).by_name("mutable_state")

        assert view.status == "active"

        await clean_core.unregister_plugin("mutable_state")

        # Same view object, live resolution
        assert view.status == "stopped"
        assert view.ready is False

    @pytest.mark.asyncio
    async def test_status_is_active_during_registration(self, clean_core):
        """Status reports 'active' immediately after register_plugin (not 'created')."""

        class WatchedPlugin(Plugin):
            def __init__(self):
                super().__init__(name="watched")

        await clean_core.register_plugin(WatchedPlugin())
        view = (await clean_core.list()).by_name("watched")

        # register_plugin calls start() internally — so status is already "active"
        assert view.status == "active"


# ---------------------------------------------------------------------------
# view.methods() tests
# ---------------------------------------------------------------------------


class TestViewMethods:
    """view.methods() returns the plugin's own public methods, excluding Plugin base methods."""

    @pytest.mark.asyncio
    async def test_methods_returns_own_public_methods(self, clean_core):
        """methods() returns a list of dicts for concrete plugin's own public methods."""
        await clean_core.register_plugin(MethodsPlugin())
        view = (await clean_core.list()).by_name("methods_plugin")

        result = await view.methods()
        assert isinstance(result, list)

        method_names = {m["name"] for m in result}
        assert "public_sync" in method_names
        assert "public_async" in method_names

    @pytest.mark.asyncio
    async def test_methods_excludes_plugin_base_methods(self, clean_core):
        """methods() excludes Plugin base-class methods (emit, config, hook, etc.)."""
        await clean_core.register_plugin(MethodsPlugin())
        view = (await clean_core.list()).by_name("methods_plugin")

        result = await view.methods()
        method_names = {m["name"] for m in result}

        base_methods = {"emit", "config", "hook", "create_background_task", "on_start", "on_stop"}
        assert method_names.isdisjoint(base_methods), (
            f"Base methods leaked into view.methods(): {method_names & base_methods}"
        )

    @pytest.mark.asyncio
    async def test_methods_excludes_private_methods(self, clean_core):
        """methods() excludes private methods (names starting with _)."""
        await clean_core.register_plugin(MethodsPlugin())
        view = (await clean_core.list()).by_name("methods_plugin")

        result = await view.methods()
        method_names = {m["name"] for m in result}

        assert "_private_method" not in method_names

    @pytest.mark.asyncio
    async def test_methods_dict_shape(self, clean_core):
        """Each entry in methods() has name, signature, parameters, return_annotation, doc."""
        await clean_core.register_plugin(MethodsPlugin())
        view = (await clean_core.list()).by_name("methods_plugin")

        result = await view.methods()
        assert len(result) >= 1

        for entry in result:
            assert "name" in entry
            assert "signature" in entry
            assert "parameters" in entry
            assert "return_annotation" in entry
            assert "doc" in entry

    @pytest.mark.asyncio
    async def test_methods_captures_parameter_names(self, clean_core):
        """Parameter names for a known method are correctly captured."""
        await clean_core.register_plugin(MethodsPlugin())
        view = (await clean_core.list()).by_name("methods_plugin")

        result = await view.methods()
        sync_entry = next(m for m in result if m["name"] == "public_sync")

        param_names = [p["name"] for p in sync_entry["parameters"]]
        assert "x" in param_names
        assert "y" in param_names

    @pytest.mark.asyncio
    async def test_methods_empty_for_base_only_plugin(self, clean_core):
        """A plugin with no own public methods returns an empty list from methods()."""

        class BarePlugin(Plugin):
            def __init__(self):
                super().__init__(name="bare")

        await clean_core.register_plugin(BarePlugin())
        view = (await clean_core.list()).by_name("bare")

        result = await view.methods()
        assert result == []

    @pytest.mark.asyncio
    async def test_methods_includes_greeter_protocol_methods(self, clean_core):
        """Typed-capability protocol methods appear in methods() for a compliant plugin."""
        await clean_core.register_plugin(GreeterPlugin())
        view = (await clean_core.list()).by_name("greeter")

        result = await view.methods()
        method_names = {m["name"] for m in result}

        assert "hello" in method_names
        assert "goodbye" in method_names
        assert "greet_sync" in method_names


# ---------------------------------------------------------------------------
# collection.capability.info() tests
# ---------------------------------------------------------------------------


class TestCapabilityInfo:
    """collection.capability.info() returns CapabilityInfo with protocol details."""

    @pytest.mark.asyncio
    async def test_info_typed_capability_returns_capability_info(self, clean_core):
        """info(name) returns a CapabilityInfo for a typed capability."""
        await clean_core.register_plugin(GreeterPlugin())
        collection = await clean_core.list()

        info = collection.capability.info("greeter")

        assert info is not None
        assert isinstance(info, CapabilityInfo)

    @pytest.mark.asyncio
    async def test_info_typed_capability_has_typed_true(self, clean_core):
        """typed=True for a protocol-backed capability."""
        await clean_core.register_plugin(GreeterPlugin())
        collection = await clean_core.list()

        info = collection.capability.info("greeter")

        assert info is not None
        assert info.typed is True

    @pytest.mark.asyncio
    async def test_info_typed_capability_has_protocol_name(self, clean_core):
        """protocol_name is set to the Protocol class name for a typed capability."""
        await clean_core.register_plugin(GreeterPlugin())
        collection = await clean_core.list()

        info = collection.capability.info("greeter")

        assert info is not None
        assert info.protocol_name == "Greeter"

    @pytest.mark.asyncio
    async def test_info_typed_capability_has_protocol_methods(self, clean_core):
        """protocol_methods contains the Protocol's method signatures."""
        await clean_core.register_plugin(GreeterPlugin())
        collection = await clean_core.list()

        info = collection.capability.info("greeter")

        assert info is not None
        assert len(info.protocol_methods) >= 2
        method_names = {m["name"] for m in info.protocol_methods}
        assert "hello" in method_names
        assert "goodbye" in method_names

    @pytest.mark.asyncio
    async def test_info_untyped_capability_has_typed_false(self, clean_core):
        """typed=False for a string-only capability."""
        await clean_core.register_plugin(UntypedPlugin())
        collection = await clean_core.list()

        info = collection.capability.info("storage")

        assert info is not None
        assert info.typed is False

    @pytest.mark.asyncio
    async def test_info_untyped_capability_has_empty_protocol_fields(self, clean_core):
        """protocol_name and protocol_methods are empty for untyped capabilities."""
        await clean_core.register_plugin(UntypedPlugin())
        collection = await clean_core.list()

        info = collection.capability.info("storage")

        assert info is not None
        assert info.protocol_name == ""
        assert info.protocol_methods == []

    @pytest.mark.asyncio
    async def test_info_unknown_capability_returns_none(self, clean_core):
        """info() returns None for a name that is not a registered capability."""
        await clean_core.register_plugin(Plugin(name="empty"))
        collection = await clean_core.list()

        assert collection.capability.info("nonexistent_capability_xyz") is None

    @pytest.mark.asyncio
    async def test_info_via_hook_filter_returns_none(self, clean_core):
        """collection.hook.info() always returns None (capability filter only)."""
        await clean_core.register_plugin(GreeterPlugin())
        collection = await clean_core.list()

        # hook filter does not support info()
        assert collection.hook.info("greeter") is None

    @pytest.mark.asyncio
    async def test_info_via_event_filter_returns_none(self, clean_core):
        """collection.event.info() always returns None (capability filter only)."""
        await clean_core.register_plugin(GreeterPlugin())
        collection = await clean_core.list()

        assert collection.event.info("greeter") is None

    @pytest.mark.asyncio
    async def test_info_providers_list(self, clean_core):
        """info.providers contains at least one provider descriptor for a registered capability."""
        await clean_core.register_plugin(GreeterPlugin())
        collection = await clean_core.list()

        info = collection.capability.info("greeter")

        assert info is not None
        assert info.provider_count >= 1
        assert len(info.providers) >= 1

        provider = info.providers[0]
        assert "name" in provider
        assert "id" in provider

    @pytest.mark.asyncio
    async def test_info_selected_provider(self, clean_core):
        """selected_provider names the currently selected capability provider."""
        await clean_core.register_plugin(GreeterPlugin())
        collection = await clean_core.list()

        info = collection.capability.info("greeter")

        assert info is not None
        assert info.selected_provider == "greeter"

    @pytest.mark.asyncio
    async def test_info_capability_name_field(self, clean_core):
        """info.name matches the capability name that was looked up."""
        await clean_core.register_plugin(GreeterPlugin())
        collection = await clean_core.list()

        info = collection.capability.info("greeter")

        assert info is not None
        assert info.name == "greeter"

    @pytest.mark.asyncio
    async def test_info_refreshed_after_register(self, clean_core):
        """After a new register, a fresh list() reflects the newly added capability."""
        await clean_core.register_plugin(Plugin(name="placeholder"))
        collection = await clean_core.list()

        assert collection.capability.info("storage") is None

        await clean_core.register_plugin(UntypedPlugin())
        fresh_collection = await clean_core.list()

        assert fresh_collection.capability.info("storage") is not None


class TestPluginViewTags:
    """The descriptive snapshot surfaces the plugin's declared tags."""

    @pytest.mark.asyncio
    async def test_view_exposes_declared_tags(self, clean_core):
        """A plugin's tags are copied onto its PluginView at collection-build time."""
        await clean_core.register_plugin(Plugin(name="tagged", tags={"local", "fast"}))

        view = (await clean_core.list()).by_name("tagged")

        assert view is not None
        assert view.tags == {"local", "fast"}

    @pytest.mark.asyncio
    async def test_view_tags_default_empty(self, clean_core):
        """A plugin that declares no tags exposes an empty set, not None."""
        await clean_core.register_plugin(Plugin(name="untagged"))

        view = (await clean_core.list()).by_name("untagged")

        assert view is not None
        assert view.tags == set()
