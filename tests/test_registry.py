"""Tests for the plugin registry: add/remove, dependencies, and load ordering."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio

from uxok import Core, Plugin
from uxok.errors import PluginError
from uxok.registry.impl import _Registry


@pytest_asyncio.fixture
async def registry():
    return _Registry()


# =============================================================================
# Add / Remove
# =============================================================================


class TestRegistryAddRemove:
    @pytest.mark.asyncio
    async def test_add_and_get(self, registry, clean_core: Core):
        p = Plugin(name="my_plugin")
        assert await registry.add(p) is True
        assert await registry.get(p.metadata.id) is p

    @pytest.mark.asyncio
    async def test_remove(self, registry, clean_core: Core):
        p = Plugin(name="removable")
        await registry.add(p)
        assert await registry.remove(p.metadata.id) is True
        assert await registry.get(p.metadata.id) is None

    @pytest.mark.asyncio
    async def test_remove_nonexistent_raises(self, registry):
        with pytest.raises(PluginError, match="not found"):
            await registry.remove(uuid4())

    @pytest.mark.asyncio
    async def test_force_remove_ignores_dependents(self, registry, clean_core: Core):
        p1 = Plugin(name="base_plugin")
        p2 = Plugin(name="dep_plugin")
        await registry.add(p1)
        await registry.add(p2, additional_dependencies={p1.metadata.id})

        with pytest.raises(PluginError, match="dependents present"):
            await registry.remove(p1.metadata.id)

        assert await registry.remove(p1.metadata.id, force=True) is True

    @pytest.mark.asyncio
    async def test_name_conflict_raises(self, registry, clean_core: Core):
        p1 = Plugin(name="same_name")
        p2 = Plugin(name="same_name")
        await registry.add(p1)
        with pytest.raises(PluginError, match="already in use by plugin"):
            await registry.add(p2)

    @pytest.mark.asyncio
    async def test_add_duplicate_id_updates_dependencies(self, registry, clean_core: Core):
        """Merging deps onto an existing plugin keeps BOTH edge directions
        consistent: the dependency is recorded, the reverse edge exists, and
        removing the dependency is blocked while the dependent is active."""
        p1 = Plugin(name="dep_base")
        p2 = Plugin(name="target")
        await registry.add(p1)
        await registry.add(p2)
        # Re-add same ID with additional deps
        assert await registry.add(p2, additional_dependencies={p1.metadata.id}) is True

        assert p1.metadata.id in await registry.dependencies(p2.metadata.id)
        assert p2.metadata.id in await registry.dependents(p1.metadata.id)
        with pytest.raises(PluginError, match="dependents present"):
            await registry.remove(p1.metadata.id)

    @pytest.mark.asyncio
    async def test_add_duplicate_id_rejects_cycle(self, registry, clean_core: Core):
        """The duplicate-add merge path is cycle-checked like any other edge
        installation."""
        p1 = Plugin(name="cyc_base")
        p2 = Plugin(name="cyc_target")
        await registry.add(p1)
        await registry.add(p2, additional_dependencies={p1.metadata.id})

        with pytest.raises(PluginError, match="[Cc]ircular"):
            await registry.add(p1, additional_dependencies={p2.metadata.id})
        # Failed merge leaves the original edges intact
        assert await registry.dependencies(p1.metadata.id) == set()

    @pytest.mark.asyncio
    async def test_contains(self, registry, clean_core: Core):
        p = Plugin(name="contained")
        await registry.add(p)
        assert await registry.contains(p.metadata.id) is True
        assert await registry.contains(uuid4()) is False

    @pytest.mark.asyncio
    async def test_all(self, registry, clean_core: Core):
        p1 = Plugin(name="all_a")
        p2 = Plugin(name="all_b")
        await registry.add(p1)
        await registry.add(p2)
        plugins = await registry.all()
        assert len(plugins) == 2


# =============================================================================
# Dependencies
# =============================================================================


class TestRegistryDependencies:
    @pytest.mark.asyncio
    async def test_dependencies(self, registry, clean_core: Core):
        p1 = Plugin(name="parent")
        p2 = Plugin(name="child")
        await registry.add(p1)
        await registry.add(p2, additional_dependencies={p1.metadata.id})

        deps = await registry.dependencies(p2.metadata.id)
        assert p1.metadata.id in deps

    @pytest.mark.asyncio
    async def test_dependents(self, registry, clean_core: Core):
        p1 = Plugin(name="parent")
        p2 = Plugin(name="child")
        await registry.add(p1)
        await registry.add(p2, additional_dependencies={p1.metadata.id})

        deps = await registry.dependents(p1.metadata.id)
        assert p2.metadata.id in deps

    @pytest.mark.asyncio
    async def test_dependency_graph(self, registry, clean_core: Core):
        p1 = Plugin(name="dep_a")
        p2 = Plugin(name="dep_b")
        await registry.add(p1)
        await registry.add(p2, additional_dependencies={p1.metadata.id})

        graph = await registry.dependency_graph()
        assert p2.metadata.id in graph
        assert p1.metadata.id in graph[p2.metadata.id]

    @pytest.mark.asyncio
    async def test_load_order_topological_sort(self, registry, clean_core: Core):
        p1 = Plugin(name="a_plugin")
        p2 = Plugin(name="b_plugin")
        p3 = Plugin(name="c_plugin")
        await registry.add(p1)
        await registry.add(p2, additional_dependencies={p1.metadata.id})
        await registry.add(p3, additional_dependencies={p2.metadata.id})

        order = await registry.load_order()
        assert order.index(p1.metadata.id) < order.index(p2.metadata.id)
        assert order.index(p2.metadata.id) < order.index(p3.metadata.id)

    @pytest.mark.asyncio
    async def test_load_order_independent_plugins(self, registry, clean_core: Core):
        p1 = Plugin(name="ind_a")
        p2 = Plugin(name="ind_b")
        await registry.add(p1)
        await registry.add(p2)
        order = await registry.load_order()
        assert len(order) == 2

    @pytest.mark.asyncio
    async def test_dependency_not_found_raises(self, registry, clean_core: Core):
        p = Plugin(name="orphan")
        with pytest.raises(PluginError, match="dependency.*not found"):
            await registry.add(p, additional_dependencies={uuid4()})


# =============================================================================
# Circular dependency detection
# =============================================================================


class TestCircularDependencyDetection:
    """Cycle rejection through the REAL mutation paths.

    The only way a cycle can form is through edges installed for a plugin
    that already has dependents — i.e. the hot-reload swap path and the
    duplicate-add merge path. Tests drive those paths instead of wiring
    corrupted graphs by hand.
    """

    @pytest.mark.asyncio
    async def test_swap_rejects_direct_cycle(self, clean_core: Core):
        """X depends on P; reloading P with a dependency on X must fail."""
        registry = _Registry()
        p = Plugin(name="cycle_p")
        x = Plugin(name="cycle_x")
        await registry.add(p)
        await registry.add(x, additional_dependencies={p.metadata.id})

        p_new = Plugin(name="cycle_p")
        p_new._assign_id(p.metadata.id)  # kernel-owned identity: rebind for swap
        with pytest.raises(PluginError, match="[Cc]ircular"):
            await registry.swap_instance(p.metadata.id, p_new, dependencies={x.metadata.id})

        # Failed swap leaves the graph intact and acyclic
        assert await registry.get(p.metadata.id) is p
        assert await registry.dependencies(p.metadata.id) == set()
        await registry.load_order()  # must not raise

    @pytest.mark.asyncio
    async def test_swap_rejects_indirect_cycle(self, clean_core: Core):
        """C→B→A chain; reloading A with a dependency on C must fail."""
        registry = _Registry()
        a = Plugin(name="chain_a")
        b = Plugin(name="chain_b")
        c = Plugin(name="chain_c")
        await registry.add(a)
        await registry.add(b, additional_dependencies={a.metadata.id})
        await registry.add(c, additional_dependencies={b.metadata.id})

        a_new = Plugin(name="chain_a")
        a_new._assign_id(a.metadata.id)  # kernel-owned identity: rebind for swap
        with pytest.raises(PluginError, match="[Cc]ircular"):
            await registry.swap_instance(a.metadata.id, a_new, dependencies={c.metadata.id})
        await registry.load_order()  # graph still acyclic

    @pytest.mark.asyncio
    async def test_swap_accepts_acyclic_edge_change(self, clean_core: Core):
        """Reloading with new edges that do NOT close a cycle succeeds and
        reconciles both edge directions."""
        registry = _Registry()
        a = Plugin(name="ok_a")
        b = Plugin(name="ok_b")
        c = Plugin(name="ok_c")
        await registry.add(a)
        await registry.add(b)
        await registry.add(c, additional_dependencies={a.metadata.id})

        c_new = Plugin(name="ok_c")
        c_new._assign_id(c.metadata.id)  # kernel-owned identity: rebind for swap
        await registry.swap_instance(c.metadata.id, c_new, dependencies={b.metadata.id})

        assert await registry.dependencies(c.metadata.id) == {b.metadata.id}
        assert c.metadata.id in await registry.dependents(b.metadata.id)
        assert c.metadata.id not in await registry.dependents(a.metadata.id)

    @pytest.mark.asyncio
    async def test_load_order_cycle_raises(self, clean_core: Core):
        """load_order() should raise on cycle in dep graph."""
        registry = _Registry()
        p1 = Plugin(name="lo_a")
        p2 = Plugin(name="lo_b")
        await registry.add(p1)
        await registry.add(p2)

        registry._dependencies[p1.metadata.id] = {p2.metadata.id}
        registry._dependencies[p2.metadata.id] = {p1.metadata.id}

        from uxok.errors import CoreError

        with pytest.raises(CoreError, match="[Cc]ircular"):
            await registry.load_order()


# =============================================================================
# Concurrency tests (lock-free atomic sections under cooperative asyncio)
# =============================================================================


class TestRegistryConcurrency:
    """Registry operations stay consistent under concurrent access.

    The registry is lock-free by design: its critical sections are
    synchronous, hence atomic under cooperative asyncio (decision #12).
    These tests assert the observable consistency that invariant provides.
    """

    @pytest.mark.asyncio
    async def test_concurrent_readers(self, registry, clean_core: Core):
        """Multiple readers can access registry simultaneously."""
        p1 = Plugin(name="reader_a")
        p2 = Plugin(name="reader_b")
        await registry.add(p1)
        await registry.add(p2)

        async def read_operation():
            """Simulate read operation."""
            plugins = await registry.all()
            order = await registry.load_order()
            contains = await registry.contains(p1.metadata.id)
            return len(plugins), len(order), contains

        # Run concurrent readers
        tasks = [read_operation() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # All should complete successfully
        assert len(results) == 10
        for plugin_count, order_count, contains in results:
            assert plugin_count == 2
            assert order_count == 2
            assert contains is True

    @pytest.mark.asyncio
    async def test_read_write_exclusion(self, registry, clean_core: Core):
        """Writers block readers and other writers."""
        write_happened = []
        read_happened = []

        async def writer_task(plugin_id):
            """Simulate write operation."""
            await asyncio.sleep(0.01)  # Let readers start first
            p = Plugin(name=f"writer_{plugin_id}")
            await registry.add(p)
            write_happened.append(plugin_id)

        async def reader_task():
            """Simulate read operation."""
            await asyncio.sleep(0.005)
            plugins = await registry.all()
            read_happened.append(len(plugins))

        # Start readers, then writer
        tasks = [reader_task() for _ in range(5)]
        tasks.append(writer_task(1))
        await asyncio.gather(*tasks)

        # All operations should complete
        assert len(write_happened) == 1
        assert len(read_happened) == 5

    @pytest.mark.asyncio
    async def test_load_order_during_add(self, registry, clean_core: Core):
        """load_order() doesn't race with add()."""
        errors = []
        results = []

        async def add_plugins(count):
            """Add plugins dynamically."""
            for i in range(count):
                try:
                    p = Plugin(name=f"dynamic_{i}")
                    await registry.add(p)
                    results.append(f"added_{i}")
                except Exception as e:
                    errors.append(e)

        async def read_load_order():
            """Read load order repeatedly."""
            for _ in range(10):
                try:
                    order = await registry.load_order()
                    results.append(f"order_{len(order)}")
                    await asyncio.sleep(0.01)
                except Exception as e:
                    errors.append(e)

        # Run concurrent operations
        await asyncio.gather(add_plugins(5), read_load_order())

        # Should complete without errors
        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_multiple_writers_serialized(self, registry, clean_core: Core):
        """Multiple writers are serialized."""
        results = []

        async def writer(plugin_num):
            """Add a plugin."""
            p = Plugin(name=f"writer_{plugin_num}")
            await registry.add(p)
            # Record that this writer completed
            results.append(plugin_num)

        # Run multiple writers concurrently
        tasks = [writer(i) for i in range(10)]
        await asyncio.gather(*tasks)

        # All writers should complete
        assert len(results) == 10

        # All plugins should be added (no lost updates)
        final_plugins = await registry.all()
        assert len(final_plugins) == 10


class TestMaxPluginsEnforcement:
    """max_plugins is a hard ceiling enforced at registration (audit M1)."""

    @pytest.mark.asyncio
    async def test_registration_past_limit_fails(self):
        from uxok import Core, Plugin
        from uxok.errors import PluginError

        core = Core(max_plugins=2)
        await core.start()
        await core.register_plugin(Plugin(name="p_one"))
        await core.register_plugin(Plugin(name="p_two"))

        with pytest.raises(PluginError, match="max_plugins limit reached"):
            await core.register_plugin(Plugin(name="p_three"))
        await core.stop()

    @pytest.mark.asyncio
    async def test_unregister_frees_a_slot(self):
        from uxok import Core, Plugin

        core = Core(max_plugins=1)
        await core.start()
        await core.register_plugin(Plugin(name="p_one"))
        await core.unregister_plugin("p_one")

        assert await core.register_plugin(Plugin(name="p_two")) is True
        await core.stop()
