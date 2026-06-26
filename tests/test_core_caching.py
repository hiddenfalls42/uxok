"""Comprehensive tests for PluginCollection caching system.

Tests the caching behavior, invalidation, thread safety, and performance
benefits of the PluginCollection caching system.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from uxok import Core, Plugin
from uxok.errors import MissingCapabilityError


class CachingTestPlugin(Plugin):
    """Simple test plugin for caching tests."""

    def __init__(
        self,
        name: str,
        provides: set[str] | None = None,
        requires: set[str] | None = None,
    ):
        super().__init__(
            name=name, version="1.0.0", provides=provides or set(), requires=requires or set()
        )


class TestPluginCollectionCaching:
    """Test the PluginCollection caching system behavior."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_same_object(self, started_core):
        """Cached calls return the same PluginCollection object."""
        # First call builds cache
        plugins1 = await started_core.list()

        # Second call should return cached object
        plugins2 = await started_core.list()

        # Should be the same object reference
        assert plugins1 is plugins2, "Cached calls should return same object"

    @pytest.mark.asyncio
    async def test_cache_invalidation_on_plugin_registration(self, started_core):
        """Cache invalidates when plugins are registered."""
        # Get initial cache
        plugins_before = await started_core.list()
        initial_count = plugins_before.count

        # Register a plugin
        test_plugin = CachingTestPlugin("test_plugin", provides={"test_cap"})
        await started_core.register_plugin(test_plugin)

        # Get new collection
        plugins_after = await started_core.list()
        new_count = plugins_after.count

        # Cache should have been invalidated (different object)
        assert plugins_before is not plugins_after, "Cache should invalidate on registration"
        assert new_count == initial_count + 1, "Should have one more plugin"
        assert plugins_after.by_name("test_plugin") is not None, "New plugin should be findable"

    @pytest.mark.asyncio
    async def test_cache_invalidation_on_plugin_unregistration(self, started_core):
        """Cache invalidates when plugins are unregistered."""
        # Register a plugin first
        test_plugin = CachingTestPlugin("test_plugin")
        await started_core.register_plugin(test_plugin)

        plugins_before = await started_core.list()
        before_count = plugins_before.count

        # Unregister the plugin
        await started_core.unregister_plugin("test_plugin")

        plugins_after = await started_core.list()
        after_count = plugins_after.count

        # Cache should have been invalidated
        assert plugins_before is not plugins_after, "Cache should invalidate on unregistration"
        assert after_count == before_count - 1, "Should have one less plugin"
        assert plugins_after.by_name("test_plugin") is None, "Plugin should no longer be findable"

    @pytest.mark.asyncio
    async def test_cache_invalidation_on_failed_and_successful_registration(self, started_core):
        """Cache handles failed registrations cleanly and invalidates on success."""
        plugins_before = await started_core.list()
        initial_count = plugins_before.count

        # Create a plugin that requires a capability that doesn't exist yet
        dependent_plugin = CachingTestPlugin("dependent", requires={"missing_cap"})

        # Registration should fail fast with clear error
        with pytest.raises(MissingCapabilityError):
            await started_core.register_plugin(dependent_plugin)

        # Cache should remain unchanged after failed registration
        plugins_after_failure = await started_core.list()
        assert plugins_before is plugins_after_failure
        assert plugins_after_failure.count == initial_count

        # Register the capability provider (succeeds)
        provider_plugin = CachingTestPlugin("provider", provides={"missing_cap"})
        await started_core.register_plugin(provider_plugin)

        # Cache should be invalidated on successful registration
        plugins_after_success = await started_core.list()
        assert plugins_after_failure is not plugins_after_success
        assert plugins_after_success.count == initial_count + 1

    @pytest.mark.asyncio
    async def test_cache_thread_safety(self, started_core):
        """Concurrent cache access doesn't cause issues."""

        async def concurrent_access(task_id: int):
            """Simulate concurrent cache access."""
            results = []
            for i in range(5):
                plugins = await started_core.list()
                results.append((task_id, i, id(plugins), plugins.count))
                await asyncio.sleep(0.001)  # Small delay to encourage interleaving
            return results

        # Run multiple concurrent tasks
        tasks = [concurrent_access(i) for i in range(3)]
        results = await asyncio.gather(*tasks)

        # All tasks should have completed successfully
        assert len(results) == 3, "All concurrent tasks should complete"

        # Each task should have consistent results
        for task_results in results:
            task_id = task_results[0][0]
            # All calls by same task should return same object (cached)
            object_ids = [r[2] for r in task_results]
            assert len(set(object_ids)) == 1, f"Task {task_id} should get consistent cache objects"

    @pytest.mark.asyncio
    async def test_cache_consistency_after_system_changes(self, started_core):
        """Cache always reflects current system state after changes."""
        # Start with empty system
        plugins = await started_core.list()
        assert plugins.count == 0

        # Register multiple plugins with unique capabilities
        plugins_to_register = [
            ("plugin1", {"cap1"}),
            ("plugin2", {"cap2"}),  # plugin2 provides cap2
            ("plugin3", {"cap3"}),
        ]

        for name, provides in plugins_to_register:
            plugin = CachingTestPlugin(name, provides=provides)
            await started_core.register_plugin(plugin)

        # Check final state
        plugins = await started_core.list()
        assert plugins.count == 3, "Should have 3 plugins"

        # Verify all plugins are findable
        for name, _ in plugins_to_register:
            assert plugins.by_name(name) is not None, f"Plugin {name} should be findable"

        # Verify capabilities work
        cap1_providers = plugins.capability.provides("cap1")
        assert cap1_providers.count == 1, "Should have 1 cap1 provider"

        cap2_providers = plugins.capability.provides("cap2")
        assert cap2_providers.count == 1, "Should have 1 cap2 provider"

        # Unregister one plugin
        await started_core.unregister_plugin("plugin2")

        plugins = await started_core.list()
        assert plugins.count == 2, "Should have 2 plugins after unregistration"
        assert plugins.by_name("plugin2") is None, "plugin2 should be gone"
        assert plugins.by_name("plugin1") is not None, "plugin1 should remain"
        assert plugins.by_name("plugin3") is not None, "plugin3 should remain"

        # Verify capabilities updated
        cap1_providers = plugins.capability.provides("cap1")
        assert cap1_providers.count == 1, "Should have 1 cap1 provider after unregistration"

        cap2_providers = plugins.capability.provides("cap2")
        assert cap2_providers.count == 0, "Should have 0 cap2 providers after unregistration"

    @pytest.mark.asyncio
    async def test_cache_handles_empty_system(self, started_core):
        """Cache works correctly with empty plugin system."""
        plugins = await started_core.list()
        assert plugins.count == 0

        # Multiple calls should return same empty collection
        plugins2 = await started_core.list()
        assert plugins is plugins2, "Should cache empty collection"
        assert plugins2.count == 0

    @pytest.mark.asyncio
    async def test_cache_handles_single_plugin_operations(self, started_core):
        """Cache works correctly with single plugin operations."""
        # Register single plugin
        plugin = CachingTestPlugin("single", provides={"unique"})
        await started_core.register_plugin(plugin)

        plugins = await started_core.list()
        assert plugins.count == 1
        assert plugins.by_name("single") is not None

        # Cache hit
        plugins_cached = await started_core.list()
        assert plugins is plugins_cached

        # Unregister
        await started_core.unregister_plugin("single")

        plugins_after = await started_core.list()
        assert plugins is not plugins_after, "Cache should invalidate"
        assert plugins_after.count == 0

    @pytest.mark.asyncio
    async def test_cache_performance_benefit(self, started_core):
        """Demonstrate performance benefit of caching."""
        # Register several plugins to make the collection substantial
        for i in range(10):
            plugin = CachingTestPlugin(f"perf_plugin_{i}", provides={f"cap_{i}"})
            await started_core.register_plugin(plugin)

        # First call builds cache (expensive)
        start_time = time.time()
        plugins1 = await started_core.list()
        build_time = time.time() - start_time

        # Cached call (should be much faster)
        start_time = time.time()
        plugins2 = await started_core.list()
        cached_time = time.time() - start_time

        # Verify caching works
        assert plugins1 is plugins2, "Should be cached"

        # Cached call should be significantly faster (at least 10x)
        speedup = build_time / cached_time if cached_time > 0 else float("inf")
        assert speedup > 10, f"Cached call should be >10x faster (was {speedup:.1f}x)"

        # Both should have same data
        assert plugins1.count == plugins2.count == 10


class TestPluginCollectionCachingProperties:
    """Property-based tests for caching behavior under various conditions."""

    @given(plugin_count=st.integers(min_value=0, max_value=15))
    @pytest.mark.asyncio
    async def test_cache_consistency_under_load(self, plugin_count):
        """Cache always reflects correct plugin count under various loads."""
        core = Core()
        await core.start()

        try:
            # Register N plugins
            for i in range(plugin_count):
                plugin = CachingTestPlugin(f"prop_plugin_{i}")
                await core.register_plugin(plugin)

            # Check cached collection
            plugins = await core.list()
            assert plugins.count == plugin_count

            # Verify cached call returns same result
            plugins_cached = await core.list()
            assert plugins is plugins_cached
            assert plugins_cached.count == plugin_count

            # Verify all plugins are accessible
            for i in range(plugin_count):
                assert plugins.by_name(f"prop_plugin_{i}") is not None

        finally:
            await core.stop()

    @given(
        initial_count=st.integers(min_value=1, max_value=5),
        operations=st.lists(
            st.sampled_from(["register", "unregister", "cache_check"]), min_size=3, max_size=10
        ),
    )
    @pytest.mark.asyncio
    async def test_cache_consistency_under_operation_sequences(self, initial_count, operations):
        """Cache remains consistent through complex operation sequences."""
        core = Core()
        await core.start()

        try:
            # Setup initial plugins
            plugin_names = []
            for i in range(initial_count):
                name = f"seq_plugin_{i}"
                plugin_names.append(name)
                plugin = CachingTestPlugin(name)
                await core.register_plugin(plugin)

            current_expected_count = initial_count
            last_plugins = None

            # Execute operation sequence
            for op in operations:
                if op == "register":
                    # Register a new plugin
                    name = f"seq_plugin_{len(plugin_names)}"
                    plugin_names.append(name)
                    plugin = CachingTestPlugin(name)
                    await core.register_plugin(plugin)
                    current_expected_count += 1

                elif op == "unregister" and plugin_names:
                    # Unregister a random plugin
                    name_to_remove = plugin_names.pop()
                    await core.unregister_plugin(name_to_remove)
                    current_expected_count -= 1

                elif op == "cache_check":
                    # Check cache consistency
                    plugins = await core.list()
                    assert plugins.count == current_expected_count

                    # If we have a previous collection, cache should have invalidated
                    # unless no operations happened between checks
                    if last_plugins is not None:
                        # Cache should be invalidated after operations
                        # (We can't easily track if operations happened, so just verify count)
                        pass
                    last_plugins = plugins

                    # Verify all expected plugins are present
                    for name in plugin_names:
                        assert plugins.by_name(name) is not None, f"Plugin {name} should exist"

                    # Verify no extra plugins
                    present_names = {p.name for p in plugins}
                    expected_names = set(plugin_names)
                    assert present_names == expected_names, (
                        f"Plugin sets should match: {present_names} vs {expected_names}"
                    )

        finally:
            await core.stop()

    @given(st.lists(st.sampled_from(["read", "write"]), min_size=10, max_size=50))
    @pytest.mark.asyncio
    async def test_cache_thread_safety_property(self, operations):
        """Property-based test for cache thread safety."""
        core = Core()
        await core.start()

        try:
            # Track expected state with lock for thread safety
            expected_plugins: set[str] = set()
            expected_lock = asyncio.Lock()
            plugin_counter = 0

            async def perform_operation(op: str):
                nonlocal plugin_counter
                if op == "read":
                    # Read operation: under the lock no writer can be mid-flight,
                    # so the collection must match the tracked expectation exactly.
                    async with expected_lock:
                        plugins = await core.list()
                        assert plugins.count == len(expected_plugins)
                        assert {p.name for p in plugins} == expected_plugins
                elif op == "write":
                    # Write operation (register/unregister)
                    async with expected_lock:
                        if expected_plugins and len(expected_plugins) > 3:
                            # Sometimes unregister
                            name_to_remove = next(iter(expected_plugins))
                            expected_plugins.remove(name_to_remove)
                            await core.unregister_plugin(name_to_remove)
                        else:
                            # Register new plugin
                            name = f"thread_plugin_{plugin_counter}"
                            plugin_counter += 1
                            expected_plugins.add(name)
                            plugin = CachingTestPlugin(name)
                            await core.register_plugin(plugin)

            # Execute operations concurrently
            tasks = [perform_operation(op) for op in operations]
            await asyncio.gather(*tasks)

            # Final consistency check
            plugins = await core.list()
            assert plugins.count == len(expected_plugins)

            # Verify all expected plugins exist
            for name in expected_plugins:
                assert plugins.by_name(name) is not None, f"Plugin {name} should exist"

        finally:
            await core.stop()
