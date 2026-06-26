"""Property-based tests for the PluginView system."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from hypothesis import given
from hypothesis import strategies as st

from uxok import Core, Plugin
from uxok.registry._plugin_proxy import PluginView

if TYPE_CHECKING:
    from uxok import Core


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stale_view(
    registry: AsyncMock, *, name: str = "gone", plugin_id: str = "gone_id"
) -> PluginView:
    """Build a PluginView whose registry always resolves to None (simulates a stale view)."""
    return PluginView(
        id=plugin_id,
        name=name,
        provides=set(),
        requires=set(),
        tags=set(),
        used_by=[],
        hooks_provided=[],
        hooks_consumed=[],
        events_published=[],
        events_subscribed=[],
        load_order=1,
        _registry=registry,
    )


# ---------------------------------------------------------------------------
# Basic unit tests
# ---------------------------------------------------------------------------


class TestPluginViewBasic:
    """Basic unit tests for PluginView functionality."""

    @pytest_asyncio.fixture
    async def core_and_plugin(self, clean_core):
        """Create a core with a test plugin for testing."""

        class ProxiedPlugin(Plugin):
            def __init__(self):
                super().__init__(name="test_plugin", version="1.0.0", provides={"test_cap"})

        plugin = ProxiedPlugin()
        await clean_core.register_plugin(plugin)
        return clean_core, plugin

    @pytest.mark.asyncio
    async def test_plugin_view_creation(self, core_and_plugin):
        """PluginView is created correctly from core.list()."""
        core, plugin = core_and_plugin

        plugins = await core.list()
        view = plugins[0]

        assert isinstance(view, PluginView)
        assert view.name == plugin.metadata.name
        assert view.status == "active"
        assert view.provides == plugin.metadata.provides
        assert view.requires == plugin.metadata.requires

    @pytest.mark.asyncio
    async def test_plugin_view_metadata_access(self, core_and_plugin):
        """Metadata fields are sync and do not trigger lazy loading."""
        core, plugin = core_and_plugin

        plugins = await core.list()
        view = plugins[0]

        # All metadata fields are synchronous
        assert view.name == plugin.metadata.name
        assert view.status == "active"
        assert view.provides == plugin.metadata.provides

    @pytest.mark.asyncio
    async def test_plugin_view_uptime_is_async(self, core_and_plugin):
        """uptime() is an async method that returns a non-negative float."""
        core, plugin = core_and_plugin

        plugins = await core.list()
        view = plugins[0]

        uptime = await view.uptime()
        assert isinstance(uptime, float)
        assert uptime >= 0

    @pytest.mark.asyncio
    async def test_plugin_view_uptime_increases(self, core_and_plugin):
        """uptime() strictly increases over time."""
        core, plugin = core_and_plugin

        plugins = await core.list()
        view = plugins[0]

        uptime1 = await view.uptime()
        await asyncio.sleep(0.1)
        uptime2 = await view.uptime()

        assert uptime2 > uptime1, "Uptime should increase over time"
        assert uptime1 >= 0

    @pytest.mark.asyncio
    async def test_plugin_view_lazy_loading(self, core_and_plugin):
        """Plugin objects are loaded lazily via _get_object()."""
        core, plugin = core_and_plugin

        plugins = await core.list()
        # _build_view pre-populates the weakref so it's not None after list()
        view = plugins[0]

        resolved_plugin = await view._get_object()
        assert view._object_ref is not None
        assert resolved_plugin == plugin

    @pytest.mark.asyncio
    async def test_plugin_view_ready_property(self, core_and_plugin):
        """ready is True for active plugins, False when the plugin is gone."""
        core, plugin = core_and_plugin

        plugins = await core.list()
        view = plugins[0]

        assert view.ready is True

        # A view that can't resolve its plugin reports ready=False
        registry = AsyncMock()
        registry.get.return_value = None
        registry.all.return_value = {}
        stale_view = _make_stale_view(registry)
        assert stale_view.ready is False

    @pytest.mark.asyncio
    async def test_plugin_view_metadata_direct_access(self, core_and_plugin):
        """Metadata fields return values directly without async wrapping."""
        core, plugin = core_and_plugin

        plugins = await core.list()
        view = plugins[0]

        name = view.name
        status = view.status
        provides = view.provides

        assert name == plugin.metadata.name
        assert status == "active"
        assert provides == plugin.metadata.provides

    @pytest.mark.asyncio
    async def test_plugin_view_id_first_resolution(self, core_and_plugin):
        """Resolution prefers ID lookup over name scan."""
        core, plugin = core_and_plugin

        plugins = await core.list()
        view = plugins[0]

        obj = await view._get_object()
        assert obj == plugin
        assert view._object_ref is not None

        # Second access uses weakref fast-path — same object returned
        obj2 = await view._get_object()
        assert obj2 is obj


# ---------------------------------------------------------------------------
# Collection operations
# ---------------------------------------------------------------------------


class TestPluginViewCollectionOperations:
    """Tests for PluginCollection operations with PluginView."""

    @pytest_asyncio.fixture
    async def core_with_plugins(self, clean_core):
        """Create a core with multiple test plugins."""

        class StoragePlugin(Plugin):
            def __init__(self):
                super().__init__(name="storage_plugin", provides={"storage"})

        class DbPlugin(Plugin):
            def __init__(self):
                super().__init__(name="db_plugin", provides={"database"})

        class ApiPlugin(Plugin):
            def __init__(self):
                super().__init__(name="api_plugin", provides={"api"}, requires={"database"})

        storage_plugin = StoragePlugin()
        db_plugin = DbPlugin()
        api_plugin = ApiPlugin()

        await clean_core.register_plugin(storage_plugin)
        await clean_core.register_plugin(db_plugin)
        await clean_core.register_plugin(api_plugin)

        return clean_core, [storage_plugin, db_plugin, api_plugin]

    @pytest.mark.asyncio
    async def test_collection_filtering_with_view(self, core_with_plugins):
        """Filtering by capability, status, and uptime works with PluginView objects."""
        core, plugins = core_with_plugins

        collection = await core.list()

        storage_plugins = collection.capability.provides("storage")
        assert len(storage_plugins) == 1
        assert storage_plugins[0].name == "storage_plugin"

        active_plugins = collection.active
        assert len(active_plugins) == 3

        # uptime_over is now async
        recent_plugins = await collection.uptime_over(-1)
        assert len(recent_plugins) == 3


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


class TestPluginViewPropertyBased:
    """Property-based tests for the PluginView system."""

    @given(
        name=st.text(min_size=1, max_size=20),
        provides=st.sets(st.text(min_size=1, max_size=10), max_size=5),
        requires=st.sets(st.text(min_size=1, max_size=10), max_size=5),
        used_by=st.lists(st.text(min_size=1, max_size=20), max_size=3),
        hooks_provided=st.lists(st.text(min_size=1, max_size=20), max_size=3),
        hooks_consumed=st.lists(st.text(min_size=1, max_size=20), max_size=3),
        events_published=st.lists(st.text(min_size=1, max_size=20), max_size=3),
        events_subscribed=st.lists(st.text(min_size=1, max_size=20), max_size=3),
        load_order=st.integers(min_value=0, max_value=100),
    )
    def test_plugin_view_metadata_preserved(
        self,
        name,
        provides,
        requires,
        used_by,
        hooks_provided,
        hooks_consumed,
        events_published,
        events_subscribed,
        load_order,
    ):
        """PluginView preserves all snapshot metadata fields correctly."""
        mock_registry = AsyncMock()
        mock_registry.get.return_value = None
        mock_registry.all.return_value = {}

        view = PluginView(
            id=f"{name}_id",
            name=name,
            provides=provides,
            requires=requires,
            tags=set(),
            used_by=used_by,
            hooks_provided=hooks_provided,
            hooks_consumed=hooks_consumed,
            events_published=events_published,
            events_subscribed=events_subscribed,
            load_order=load_order,
            _registry=mock_registry,
        )

        assert view.name == name
        assert view.provides == provides
        assert view.requires == requires
        assert view.used_by == used_by
        assert view.hooks_provided == hooks_provided
        assert view.hooks_consumed == hooks_consumed
        assert view.events_published == events_published
        assert view.events_subscribed == events_subscribed
        assert view.load_order == load_order
        # No live plugin → status is "stopped", not "active"
        assert view.status == "stopped"
        assert view.ready is False

    @given(collection_size=st.integers(min_value=1, max_value=10))
    @pytest.mark.asyncio
    async def test_plugin_view_collection_operations_property(self, collection_size):
        """Collection operations are consistent for any valid collection size."""
        mock_registry = AsyncMock()
        mock_registry.get = AsyncMock(return_value=None)
        mock_registry.all = AsyncMock(return_value={})

        views = [
            PluginView(
                id=f"plugin_{i}_id",
                name=f"plugin_{i}",
                provides={f"cap_{i}"},
                requires=set(),
                tags=set(),
                used_by=[],
                hooks_provided=[],
                hooks_consumed=[],
                events_published=[],
                events_subscribed=[],
                load_order=i,
                _registry=mock_registry,
            )
            for i in range(collection_size)
        ]

        from uxok.registry._plugin_proxy import PluginCollection

        collection = PluginCollection(views)

        assert len(collection) == collection_size
        # All views have no live plugin, so none are "active"
        assert len(collection.active) == 0

    @given(
        hooks_consumed=st.sets(st.text(min_size=1, max_size=20), max_size=5),
        events_published=st.sets(st.text(min_size=1, max_size=20), max_size=5),
    )
    @pytest.mark.asyncio
    async def test_hooks_consumed_events_published_preserved(
        self, hooks_consumed, events_published
    ):
        """hooks_consumed and events_published snapshot fields survive the list() call."""

        class TestPlugin(Plugin):
            def __init__(self, hc, ep):
                super().__init__(name="test", hooks_consumed=hc, events_published=ep)

        core = Core()
        plugin = TestPlugin(hooks_consumed, events_published)
        await core.register_plugin(plugin)

        plugins = await core.list()
        view = plugins[0]

        assert set(view.hooks_consumed) == hooks_consumed
        assert set(view.events_published) == events_published


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestPluginViewIntegration:
    """Integration tests for PluginView with real plugin scenarios."""

    @pytest.mark.asyncio
    async def test_plugin_view_with_real_plugins(self, clean_core):
        """PluginView works correctly with a multi-plugin dependency graph."""

        class StoragePlugin(Plugin):
            def __init__(self):
                super().__init__(name="storage", provides={"storage"})

        class DbPlugin(Plugin):
            def __init__(self):
                super().__init__(name="database", provides={"database"}, requires={"storage"})

        class ApiPlugin(Plugin):
            def __init__(self):
                super().__init__(name="api", provides={"api"}, requires={"database"})

        core = clean_core

        await core.register_plugin(StoragePlugin())
        await core.register_plugin(DbPlugin())
        await core.register_plugin(ApiPlugin())

        plugins = await core.list()
        assert len(plugins) == 3

        storage_views = plugins.capability.provides("storage")
        assert len(storage_views) == 1
        assert storage_views[0].name == "storage"

        db_views = plugins.capability.consumes("storage")
        assert len(db_views) == 1
        assert db_views[0].name == "database"

        for view in plugins:
            obj = await view._get_object()
            assert obj is not None
            assert obj.metadata.name == view.name

    @pytest.mark.asyncio
    async def test_plugin_view_performance(self, clean_core):
        """Metadata access does not trigger lazy loading; lazy loading is fast thereafter."""
        import time

        core = clean_core

        for i in range(10):

            class IndexedPlugin(Plugin):
                def __init__(self, idx):
                    super().__init__(name=f"plugin_{idx}", provides={f"cap_{idx}"})

            plugin = IndexedPlugin(i)
            await core.register_plugin(plugin)

        collection = await core.list()
        assert len(collection) == 10

        start = time.time()
        for view in collection:
            _ = view.name
            _ = view.provides
            _ = view.status
        metadata_time = time.time() - start

        start = time.time()
        await collection[0]._get_object()
        lazy_load_time = time.time() - start

        assert metadata_time < lazy_load_time * 10

    @pytest.mark.asyncio
    async def test_plugin_view_is_not_a_handle(self, clean_core):
        """The view is a description, not a handle: no __getattr__ delegation, and no
        invocation members to reach the live instance (RFC 0001 §3.2.2)."""

        class WithMethod(Plugin):
            def __init__(self):
                super().__init__(name="with_method")

            def do_thing(self):
                return "done"

        await clean_core.register_plugin(WithMethod())
        view = (await clean_core.list()).by_name("with_method")

        # A plugin method is not reachable as an attribute on the view...
        with pytest.raises(AttributeError):
            _ = view.do_thing

        # ...and the invocation members are gone entirely — no backdoor to the instance.
        assert not hasattr(view, "call")
        assert not hasattr(view, "get_object")


# ---------------------------------------------------------------------------
# Cache freshness tests (caching is unchanged — keep these intact)
# ---------------------------------------------------------------------------


class TestPluginCollectionCachingProperties:
    """Property-based tests for PluginCollection caching behavior (unchanged)."""

    class MockPlugin(Plugin):
        """Simple test plugin for property-based caching tests."""

        def __init__(
            self,
            name: str,
            provides: set[str] | None = None,
            requires: set[str] | None = None,
        ):
            super().__init__(
                name=name,
                version="1.0.0",
                provides=provides or set(),
                requires=requires or set(),
            )

        async def some_method(self):
            """Mock method for testing."""
            return "mock_result"

    @given(
        plugin_count=st.integers(min_value=0, max_value=12),
        operation_sequence=st.lists(
            st.sampled_from(["register", "unregister", "list_check", "filter_check"]),
            min_size=5,
            max_size=15,
        ),
    )
    @pytest.mark.asyncio
    async def test_cache_consistency_under_complex_operations(
        self, plugin_count, operation_sequence
    ):
        """Cache remains consistent through complex sequences of operations."""
        from uxok import Core

        core = Core()
        await core.start()

        try:
            plugin_names = set()
            for i in range(plugin_count):
                name = f"cache_plugin_{i}"
                plugin_names.add(name)
                plugin = self.MockPlugin(name, provides={f"cap_{i}"})
                await core.register_plugin(plugin)

            expected_count = plugin_count
            last_collection = None
            cache_invalidated_since_last_check = False

            for op_index, op in enumerate(operation_sequence):
                if op == "register":
                    name = f"cache_plugin_{plugin_count}_{len(plugin_names)}_{op_index}"
                    plugin_names.add(name)
                    capability_name = f"cap_{plugin_count}_{len(plugin_names)}_{op_index}"
                    plugin = self.MockPlugin(name, provides={capability_name})
                    await core.register_plugin(plugin)
                    expected_count += 1
                    cache_invalidated_since_last_check = True

                elif op == "unregister" and plugin_names:
                    name_to_remove = next(iter(plugin_names))
                    plugin_names.remove(name_to_remove)
                    await core.unregister_plugin(name_to_remove)
                    expected_count -= 1
                    cache_invalidated_since_last_check = True

                elif op == "list_check":
                    collection = await core.list()
                    assert collection.count == expected_count

                    if cache_invalidated_since_last_check and last_collection is not None:
                        assert collection is not last_collection, (
                            "Cache should invalidate after register/unregister operations"
                        )

                    last_collection = collection
                    cache_invalidated_since_last_check = False

                    for name in plugin_names:
                        assert collection.by_name(name) is not None

                    actual_names = {p.name for p in collection}
                    assert actual_names == plugin_names

                elif op == "filter_check":
                    collection = await core.list()

                    active = collection.active
                    assert active.count == expected_count

                    cap_0_providers = collection.capability.provides("cap_0")
                    expected_cap0 = 1 if "cache_plugin_0" in plugin_names else 0
                    assert cap_0_providers.count == expected_cap0

                    if plugin_names:
                        first_name = next(iter(plugin_names))
                        found = collection.by_name(first_name)
                        assert found is not None
                        assert found.name == first_name

        finally:
            await core.stop()

    @given(
        concurrent_operations=st.lists(
            st.tuples(
                st.sampled_from(["register", "list", "filter"]),
                st.integers(min_value=0, max_value=4),
            ),
            min_size=10,
            max_size=30,
        )
    )
    @pytest.mark.asyncio
    async def test_cache_thread_safety_property_based(self, concurrent_operations):
        """Cache stays consistent under concurrent register/list/filter operations."""
        from uxok import Core

        core = Core()
        await core.start()

        try:
            registered_plugins = set()
            plugin_counter = 0
            total_registers = sum(1 for op, _ in concurrent_operations if op == "register")
            registers_per_param = {
                param: sum(1 for op, p in concurrent_operations if op == "register" and p == param)
                for _, param in concurrent_operations
            }

            async def execute_operation(op_type: str, op_param: int):
                nonlocal plugin_counter

                if op_type == "register":
                    name = f"thread_plugin_{plugin_counter}"
                    plugin_counter += 1
                    provides = {f"thread_cap_{op_param}"}

                    plugin = self.MockPlugin(name, provides=provides)
                    assert await core.register_plugin(plugin) is True
                    registered_plugins.add(name)

                elif op_type == "list":
                    collection = await core.list()
                    assert len(registered_plugins) <= collection.count <= total_registers

                elif op_type == "filter":
                    collection = await core.list()
                    cap_providers = collection.capability.provides(f"thread_cap_{op_param}")
                    assert cap_providers.count <= registers_per_param[op_param]
                    assert collection.active.count == collection.count

            tasks = [
                execute_operation(op_type, op_param) for op_type, op_param in concurrent_operations
            ]
            await asyncio.gather(*tasks)

            final_collection = await core.list()
            assert final_collection.count == total_registers
            assert len(registered_plugins) == total_registers
            assert {p.name for p in final_collection} == registered_plugins
            assert final_collection.active.count == total_registers

        finally:
            await core.stop()

    @pytest.mark.asyncio
    @given(plugin_count=st.integers(min_value=1, max_value=5))
    async def test_cache_consistency_basic(self, plugin_count):
        """Basic cache consistency: hit returns same object; invalidation returns new object."""
        from uxok import Core

        core = Core()
        await core.start()

        try:
            for i in range(plugin_count):
                plugin = self.MockPlugin(f"cache_test_{i}", provides={f"cap_{i}"})
                assert await core.register_plugin(plugin) is True

            collection = await core.list()
            assert collection.count == plugin_count

            cached_collection = await core.list()
            assert cached_collection is collection

            new_plugin = self.MockPlugin("cache_invalidator", provides={"invalidate_cap"})
            await core.register_plugin(new_plugin)

            new_collection = await core.list()
            assert new_collection is not collection
            assert new_collection.count == plugin_count + 1

            cached_after_invalidation = await core.list()
            assert cached_after_invalidation is new_collection

        finally:
            await core.stop()


# ---------------------------------------------------------------------------
# Honesty invariants (decision #11 — view never fabricates)
# ---------------------------------------------------------------------------


class TestProxyHonesty:
    """The view never fabricates: every field reflects real state (decision #11)."""

    @pytest.mark.asyncio
    async def test_status_reflects_real_lifecycle(self, clean_core):
        core = clean_core

        class LifecyclePlugin(Plugin):
            def __init__(self):
                super().__init__(name="lifecycle")

        plugin = LifecyclePlugin()
        await core.register_plugin(plugin)

        view = (await core.list()).by_name("lifecycle")
        assert view.status == "active"
        assert view.ready is True

        # A never-started instance reports "created".
        unstarted = LifecyclePlugin.__new__(LifecyclePlugin)
        from uxok.registry._plugin_proxy import _plugin_status_from_instance

        assert _plugin_status_from_instance(unstarted) == "created"

    @pytest.mark.asyncio
    async def test_size_field_is_gone(self, clean_core):
        await clean_core.register_plugin(Plugin(name="anyone"))
        view = (await clean_core.list()).by_name("anyone")
        assert not hasattr(view, "size")

    @pytest.mark.asyncio
    async def test_no_getattr_delegation(self, clean_core):
        """Unknown attributes fail at access time — no async wrapper magic, and the
        view exposes no invocation members (RFC 0001 §3.2.2)."""

        class WithMethod(Plugin):
            def __init__(self):
                super().__init__(name="with_method")

            def do_thing(self):
                return "done"

        await clean_core.register_plugin(WithMethod())
        view = (await clean_core.list()).by_name("with_method")

        with pytest.raises(AttributeError):
            _ = view.do_thing

        assert not hasattr(view, "call")
        assert not hasattr(view, "get_object")


# ---------------------------------------------------------------------------
# Resolution + filtering internals (still live — they back the benign reads and
# index-less sub-collections; covered here directly rather than incidentally)
# ---------------------------------------------------------------------------


class _FakePlugin:
    """Weakref-able stand-in with just the metadata `_get_object` reads."""

    def __init__(self, name: str, pid: object) -> None:
        self.metadata = SimpleNamespace(name=name, id=pid)


class TestGetObjectNameScan:
    """``_get_object`` name-scan fallback (taken when the id is not a UUID).

    Backs the benign live reads (``uptime``/``methods``) and ``status``.
    """

    @pytest.mark.asyncio
    async def test_name_scan_single_match(self):
        target = _FakePlugin("target", uuid4())
        registry = AsyncMock()
        registry.all = AsyncMock(return_value={"k": target})

        view = _make_stale_view(registry, name="target", plugin_id="not-a-uuid")
        assert await view._get_object() is target

    @pytest.mark.asyncio
    async def test_name_scan_ambiguous_raises(self):
        dup1, dup2 = _FakePlugin("dup", uuid4()), _FakePlugin("dup", uuid4())
        registry = AsyncMock()
        registry.all = AsyncMock(return_value={"a": dup1, "b": dup2})

        view = _make_stale_view(registry, name="dup", plugin_id="not-a-uuid")
        with pytest.raises(ValueError, match="ambiguous"):
            await view._get_object()

    @pytest.mark.asyncio
    async def test_name_scan_no_match_returns_none(self):
        registry = AsyncMock()
        registry.all = AsyncMock(return_value={})

        view = _make_stale_view(registry, name="absent", plugin_id="not-a-uuid")
        assert await view._get_object() is None


class TestFilterProxySlowPath:
    """``_FilterProxy`` linear scan on an index-less collection (build_indexes=False)."""

    def _view(self, i: int, **kw) -> PluginView:
        return PluginView(
            id=str(i),
            name=f"p{i}",
            provides=kw.get("provides", set()),
            requires=kw.get("requires", set()),
            tags=set(),
            used_by=[],
            hooks_provided=kw.get("hooks_provided", []),
            hooks_consumed=kw.get("hooks_consumed", []),
            events_published=kw.get("events_published", []),
            events_subscribed=kw.get("events_subscribed", []),
            load_order=i,
            _registry=AsyncMock(),
        )

    def test_slow_path_provides_and_consumes(self):
        from uxok.registry._plugin_proxy import PluginCollection

        views = [
            self._view(0, provides={"cap"}, hooks_provided=["h"], events_published=["e"]),
            self._view(1, requires={"cap"}, hooks_consumed=["h"], events_subscribed=["e"]),
        ]
        coll = PluginCollection(views, build_indexes=False)  # forces the index-less path

        assert coll.capability.provides("cap").names == ["p0"]
        assert coll.hook.provides("h").names == ["p0"]
        assert coll.event.provides("e").names == ["p0"]
        assert coll.capability.consumes("cap").names == ["p1"]
        assert coll.hook.consumes("h").names == ["p1"]
        assert coll.event.consumes("e").names == ["p1"]
