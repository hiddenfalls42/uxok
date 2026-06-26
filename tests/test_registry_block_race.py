"""Test that block()/unblock() are thread-safe with add()."""

import asyncio

import pytest

from uxok import Core, Plugin


class TestRegistryBlockRaceConditions:
    """Test race conditions between block/unblock and add operations."""

    @pytest.mark.asyncio
    async def test_block_race_with_add(self, started_core: Core):
        """Test that concurrent block() and add() don't race.

        This is the main regression test for the TOCTOU bug.
        """
        plugin_name = "race_test_plugin"
        plugin = Plugin(name=plugin_name)

        # Run block() and add() concurrently
        block_task = asyncio.create_task(started_core._registry.block(plugin_name))
        add_task = asyncio.create_task(started_core._registry.add(plugin))

        # Wait for both to complete
        await asyncio.gather(block_task, add_task)

        # Plugin should either be successfully registered OR blocked, never both
        is_registered = await started_core._registry.contains(plugin.metadata.id)
        is_blocked = started_core._registry.is_blocked(plugin_name)

        # At most one should be true
        assert not (is_registered and is_blocked), (
            "Plugin was both registered AND blocked - race condition detected!"
        )

    @pytest.mark.asyncio
    async def test_unblock_race_with_add(self, started_core: Core):
        """Test that concurrent unblock() and add() work correctly."""
        plugin_name = "unblock_race_plugin"

        # Block the plugin first
        await started_core._registry.block(plugin_name)

        plugin = Plugin(name=plugin_name)

        # Run unblock() and add() concurrently
        unblock_task = asyncio.create_task(started_core._registry.unblock(plugin_name))
        add_task = asyncio.create_task(started_core._registry.add(plugin))

        await asyncio.gather(unblock_task, add_task)

        # Plugin should be registered (unblock should happen before or during add)
        is_registered = await started_core._registry.contains(plugin.metadata.id)
        assert is_registered, "Plugin should be registered after unblock+add"

    @pytest.mark.asyncio
    async def test_concurrent_blocks_same_plugin(self, started_core: Core):
        """Test that multiple concurrent block() calls are safe."""
        plugin_name = "concurrent_block_plugin"

        # Block the same plugin 100 times concurrently
        tasks = [asyncio.create_task(started_core._registry.block(plugin_name)) for _ in range(100)]

        results = await asyncio.gather(*tasks)

        # Should still be blocked (no corruption)
        assert started_core._registry.is_blocked(plugin_name)

        # Unblock should work correctly
        result = await started_core._registry.unblock(plugin_name)
        assert result is True
        assert not started_core._registry.is_blocked(plugin_name)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("iterations", [10, 50, 100])
    async def test_stress_concurrent_block_add_unblock(self, started_core: Core, iterations):
        """Stress test: Many concurrent block/add/unblock operations."""
        plugin_name = f"stress_plugin_{iterations}"
        plugin = Plugin(name=plugin_name)

        async def random_operation():
            """Perform random block/unblock/add operations."""
            import random

            operation = random.choice(["block", "unblock", "add"])
            if operation == "block":
                await started_core._registry.block(plugin_name)
            elif operation == "unblock":
                await started_core._registry.unblock(plugin_name)
            else:  # add
                # Create fresh plugin for each add attempt
                p = Plugin(name=plugin_name)
                await started_core._registry.add(p)

        # Run many random operations concurrently
        tasks = [asyncio.create_task(random_operation()) for _ in range(iterations)]

        await asyncio.gather(*tasks, return_exceptions=True)

        # Final state should be consistent
        is_blocked = started_core._registry.is_blocked(plugin_name)
        is_registered = await started_core._registry.contains(plugin.metadata.id)

        # Can't be both blocked and registered
        assert not (is_blocked and is_registered), "Corruption: Plugin both blocked AND registered"

    @pytest.mark.asyncio
    async def test_block_add_sequence_multiple_plugins(self, started_core: Core):
        """Test blocking and adding multiple plugins concurrently."""
        plugin_names = [f"plugin_{i}" for i in range(10)]
        plugins = [Plugin(name=name) for name in plugin_names]

        # Block half the plugins and add all concurrently
        tasks = []
        for i, plugin in enumerate(plugins):
            if i % 2 == 0:
                tasks.append(asyncio.create_task(started_core._registry.block(plugin_names[i])))
            tasks.append(asyncio.create_task(started_core._registry.add(plugin)))

        await asyncio.gather(*tasks)

        # Verify state consistency
        for i, name in enumerate(plugin_names):
            is_blocked = started_core._registry.is_blocked(name)
            is_registered = await started_core._registry.contains(plugins[i].metadata.id)

            # Even plugins should be blocked, odd should be registered
            if i % 2 == 0:
                assert is_blocked, f"Plugin {name} should be blocked"
                assert not is_registered, f"Plugin {name} should not be registered"
            else:
                assert not is_blocked, f"Plugin {name} should not be blocked"
                assert is_registered, f"Plugin {name} should be registered"
