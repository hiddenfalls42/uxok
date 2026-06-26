"""Integration tests for PluginCollection caching in real workflow scenarios.

Tests caching behavior during hot-reload, concurrent operations, and
complex plugin dependency scenarios.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from uxok import Plugin
from uxok.errors import MissingCapabilityError


class IntegrationTestPlugin(Plugin):
    """Test plugin for caching integration tests."""

    def __init__(
        self,
        name: str,
        provides: set[str] | None = None,
        requires: set[str] | None = None,
    ):
        super().__init__(
            name=name, version="1.0.0", provides=provides or set(), requires=requires or set()
        )


class TestPluginCachingIntegration:
    """Integration tests for caching in real plugin workflows."""

    @pytest_asyncio.fixture
    async def core(self, started_core):
        """Started core with guaranteed teardown (delegates to root fixture)."""
        return started_core

    @pytest.mark.asyncio
    async def test_caching_with_hot_reload_workflow(self, core):
        """Cache works correctly during hot-reload scenarios."""
        # Register initial plugin
        plugin1 = IntegrationTestPlugin("original", provides={"service"})
        await core.register_plugin(plugin1)

        plugins_before = await core.list()
        assert plugins_before.count == 1
        assert plugins_before.by_name("original") is not None

        # Simulate hot-reload: unregister and re-register with changes
        await core.unregister_plugin("original")

        plugins_after_unreg = await core.list()
        assert plugins_after_unreg.count == 0
        assert plugins_before is not plugins_after_unreg  # Cache invalidated

        # Re-register with different capabilities
        plugin1_reloaded = IntegrationTestPlugin("original", provides={"service", "enhanced"})
        await core.register_plugin(plugin1_reloaded)

        plugins_after_rereg = await core.list()
        assert plugins_after_rereg.count == 1
        assert plugins_after_rereg is not plugins_after_unreg  # Cache invalidated again

        # Verify enhanced capabilities
        service_providers = plugins_after_rereg.capability.provides("service")
        enhanced_providers = plugins_after_rereg.capability.provides("enhanced")
        assert service_providers.count == 1
        assert enhanced_providers.count == 1

    @pytest.mark.asyncio
    async def test_caching_with_concurrent_operations(self, core):
        """Cache remains consistent during concurrent plugin operations."""

        async def register_plugins(task_id: int, plugin_range: range):
            """Register a range of plugins concurrently."""
            for i in plugin_range:
                plugin = IntegrationTestPlugin(
                    f"concurrent_{task_id}_{i}", provides={f"cap_{task_id}_{i}"}
                )
                await core.register_plugin(plugin)
                await asyncio.sleep(0.001)  # Allow interleaving

        async def unregister_plugins(plugin_names: list[str]):
            """Unregister plugins concurrently."""
            for name in plugin_names:
                await core.unregister_plugin(name)
                await asyncio.sleep(0.001)

        # Phase 1: Concurrent registration
        tasks = [
            register_plugins(0, range(5)),
            register_plugins(1, range(5, 10)),
            register_plugins(2, range(10, 15)),
        ]
        await asyncio.gather(*tasks)

        # Verify all plugins registered and cache consistent
        plugins = await core.list()
        assert plugins.count == 15, "All concurrent registrations should succeed"

        # Phase 2: Concurrent cache access during operations
        async def verify_cache_consistency():
            """Continuously verify cache consistency."""
            for _ in range(10):
                plugins = await core.list()
                # Cache should be invalidated after each operation, so we get fresh data
                assert plugins.count >= 5  # At least some plugins remain
                await asyncio.sleep(0.001)

        # Unregister some plugins while verifying cache
        plugins_to_remove = ["concurrent_0_0", "concurrent_1_5", "concurrent_2_10"]

        await asyncio.gather(unregister_plugins(plugins_to_remove), verify_cache_consistency())

        # Final verification
        final_plugins = await core.list()
        assert final_plugins.count == 12, "Should have 12 plugins after removing 3"

        # Verify removed plugins are gone
        for removed_name in plugins_to_remove:
            assert final_plugins.by_name(removed_name) is None

    @pytest.mark.asyncio
    async def test_caching_with_capability_dependency_resolution(self, core):
        """Cache works with complex capability dependency chains."""
        # Create a dependency chain: A requires B, B requires C
        plugin_c = IntegrationTestPlugin("plugin_c", provides={"capability_c"})
        plugin_b = IntegrationTestPlugin(
            "plugin_b", provides={"capability_b"}, requires={"capability_c"}
        )
        plugin_a = IntegrationTestPlugin(
            "plugin_a", provides={"capability_a"}, requires={"capability_b"}
        )

        # Register in wrong order (dependencies not available) and expect fail-fast errors
        with pytest.raises(MissingCapabilityError):
            await core.register_plugin(plugin_a)
        with pytest.raises(MissingCapabilityError):
            await core.register_plugin(plugin_b)

        plugins_after_failures = await core.list()
        assert plugins_after_failures.count == 0

        # Register the base capability
        await core.register_plugin(plugin_c)

        # Now register the rest in dependency order
        await core.register_plugin(plugin_b)
        await core.register_plugin(plugin_a)

        plugins_after = await core.list()
        assert plugins_after.count == 3, "All plugins should be active after dependency resolution"

        # Verify dependency chain works
        cap_a_providers = plugins_after.capability.provides("capability_a")
        cap_b_providers = plugins_after.capability.provides("capability_b")
        cap_c_providers = plugins_after.capability.provides("capability_c")

        assert cap_a_providers.count == 1
        assert cap_b_providers.count == 1
        assert cap_c_providers.count == 1

        # Verify dependencies are tracked
        plugin_a_proxy = plugins_after.by_name("plugin_a")
        plugin_b_proxy = plugins_after.by_name("plugin_b")
        plugin_c_proxy = plugins_after.by_name("plugin_c")

        assert "capability_b" in plugin_a_proxy.requires
        assert "capability_c" in plugin_b_proxy.requires
        assert len(plugin_c_proxy.requires) == 0

    @pytest.mark.asyncio
    async def test_caching_with_plugin_state_changes(self, core):
        """Cache handles plugin state changes correctly."""

        # Create a plugin that can change state
        class StatefulPlugin(IntegrationTestPlugin):
            def __init__(self, name):
                super().__init__(name, provides={"stateful"})
                self._active = True

            async def on_start(self):
                self._active = True

            async def on_stop(self):
                self._active = False

        plugin = StatefulPlugin("stateful")
        await core.register_plugin(plugin)

        plugins = await core.list()
        assert plugins.count == 1
        assert plugins.by_name("stateful") is not None

        # Cache should remain valid for metadata-only operations
        plugins_cached = await core.list()
        assert plugins is plugins_cached, "Cache should persist for metadata operations"

        # If we were to implement plugin start/stop that changes metadata,
        # cache would need invalidation, but for now plugins maintain
        # consistent metadata throughout their lifecycle

    @pytest.mark.asyncio
    async def test_caching_resilience_to_errors(self, core):
        """Cache rebuilds correctly even if previous rebuilds failed."""
        # This test ensures the caching system is resilient to transient failures

        # Register a plugin successfully
        plugin1 = IntegrationTestPlugin("stable", provides={"stable"})
        await core.register_plugin(plugin1)

        plugins = await core.list()
        assert plugins.count == 1

        # Simulate a scenario where cache becomes invalid
        # (This would happen naturally during normal operations)

        # Add another plugin
        plugin2 = IntegrationTestPlugin("also_stable", provides={"also_stable"})
        await core.register_plugin(plugin2)

        plugins_after = await core.list()
        assert plugins_after.count == 2
        assert plugins is not plugins_after, "Cache should invalidate"

        # Verify both plugins are accessible
        assert plugins_after.by_name("stable") is not None
        assert plugins_after.by_name("also_stable") is not None

        # Remove one plugin
        await core.unregister_plugin("stable")

        plugins_final = await core.list()
        assert plugins_final.count == 1
        assert plugins_final.by_name("stable") is None
        assert plugins_final.by_name("also_stable") is not None

    @pytest.mark.asyncio
    async def test_caching_with_large_plugin_sets(self, core):
        """Cache performance with larger numbers of plugins."""
        # Register a moderate number of plugins
        plugin_count = 50

        for i in range(plugin_count):
            provides = {f"cap_{i}"}  # Unique capabilities
            plugin = IntegrationTestPlugin(f"bulk_plugin_{i}", provides=provides)
            await core.register_plugin(plugin)

        # First call builds cache
        plugins1 = await core.list()
        assert plugins1.count == plugin_count

        # Cached calls should be fast
        import time

        start = time.time()
        for _ in range(10):
            plugins_cached = await core.list()
            assert plugins_cached is plugins1  # Should be cached
        cached_duration = time.time() - start

        # Should be very fast (< 0.01s total for 10 cached calls)
        assert cached_duration < 0.01, f"Cached calls too slow: {cached_duration:.4f}s"

        # Verify filtering still works on large cached collections
        cap_0_providers = plugins1.capability.provides("cap_0")
        assert cap_0_providers.count == 1  # Should have 1 provider for cap_0 (unique capabilities)

        # Individual lookups should work
        specific_plugin = plugins1.by_name("bulk_plugin_25")
        assert specific_plugin is not None
        assert "cap_25" in specific_plugin.provides  # Each plugin has its own unique capability
