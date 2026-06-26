"""Property-based tests for plugin blocking functionality."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from uxok import Core, Plugin


class BlockableTestPlugin(Plugin):
    """Test plugin that can be blocked."""

    def __init__(self, plugin_name: str = "test_plugin"):
        super().__init__(name=plugin_name)
        self.initialized = False
        self.shutdown_called = False

    async def on_start(self) -> None:
        """Mark as initialized."""
        self.initialized = True

    async def on_stop(self) -> None:
        """Mark shutdown."""
        self.shutdown_called = True


class TestPluginBlockingProperties:
    """Property-based tests for plugin blocking."""

    @pytest.mark.asyncio
    @given(
        plugin_names=st.sets(
            st.text(min_size=2, max_size=10, alphabet="abcdefghijklmnopqrstuvwxyz"),
            min_size=1,
            max_size=3,
        )
    )
    async def test_block_multiple_plugins_consistency(self, plugin_names):
        """Test blocking multiple plugins maintains consistent state."""
        core = Core()

        # Block all plugins
        for name in plugin_names:
            await core._registry.block(name)
            assert core._registry.is_blocked(name)

        # Verify all are blocked
        for name in plugin_names:
            assert core._registry.is_blocked(name)

        await core.start()

        # Blocked plugins are refused: register_plugin returns False.
        for name in plugin_names:
            plugin = BlockableTestPlugin(name)
            assert await core.register_plugin(plugin) is False
            assert not plugin.initialized

        await core.stop()

    @pytest.mark.asyncio
    @given(
        block_unblock_sequences=st.lists(
            st.fixed_dictionaries(
                {
                    "action": st.one_of(st.just("block"), st.just("unblock")),
                    "plugin_name": st.text(
                        min_size=2, max_size=8, alphabet="abcdefghijklmnopqrstuvwxyz"
                    ),
                }
            ),
            min_size=1,
            max_size=10,
        )
    )
    async def test_block_unblock_sequence_properties(self, block_unblock_sequences):
        """Test properties of block/unblock sequences."""
        core = Core()

        blocked_set = set()

        for action in block_unblock_sequences:
            plugin_name = action["plugin_name"]

            if action["action"] == "block":
                await core._registry.block(plugin_name)
                blocked_set.add(plugin_name)
                assert core._registry.is_blocked(plugin_name)
            else:  # unblock
                was_blocked = await core._registry.unblock(plugin_name)
                was_actually_blocked = plugin_name in blocked_set
                blocked_set.discard(plugin_name)

                # Should return True if was actually blocked
                assert was_blocked == was_actually_blocked
                assert not core._registry.is_blocked(plugin_name)

    @pytest.mark.asyncio
    @given(
        blocked_plugins=st.sets(
            st.text(min_size=2, max_size=8, alphabet="abcdefghijklmnopqrstuvwxyz"),
            min_size=1,
            max_size=2,
        ),
        allowed_plugins=st.sets(
            st.text(min_size=2, max_size=8, alphabet="abcdefghijklmnopqrstuvwxyz"),
            min_size=1,
            max_size=2,
        ),
    )
    async def test_mixed_blocking_registration(self, blocked_plugins, allowed_plugins):
        """Test registration with mixed blocked/allowed plugins."""
        core = Core()

        # Block specific plugins
        for name in blocked_plugins:
            await core._registry.block(name)

        await core.start()

        # Try to register all plugins: blocked names are refused (False),
        # everything else registers (True). Names in both sets are blocked.
        for name in blocked_plugins.union(allowed_plugins):
            plugin = BlockableTestPlugin(name)
            expected = name not in blocked_plugins
            assert await core.register_plugin(plugin) is expected

        await core.stop()

    @pytest.mark.asyncio
    @given(
        blocked_from_config=st.sets(
            st.text(min_size=2, max_size=8, alphabet="abcdefghijklmnopqrstuvwxyz"),
            min_size=0,
            max_size=5,
        )
    )
    async def test_config_blocking_initialization(self, blocked_from_config):
        """Test that config blocking is applied during initialization."""
        core = Core(blocked_plugins=frozenset(blocked_from_config))

        # All config-specified plugins should be blocked immediately
        for name in blocked_from_config:
            assert core._registry.is_blocked(name)

        # Plugins not in config should not be blocked
        test_names = {"not_in_config1", "not_in_config2"}
        for name in test_names:
            assert not core._registry.is_blocked(name)

        await core.start()
        await core.stop()

    @pytest.mark.asyncio
    @given(
        plugin_count=st.integers(min_value=1, max_value=5),
        operation_count=st.integers(min_value=1, max_value=10),
    )
    async def test_blocking_during_runtime(self, plugin_count, operation_count):
        """Test blocking/unblocking during runtime."""
        core = Core()
        await core.start()

        # Register some plugins
        plugins = []
        plugin_names = [f"runtime_plugin_{i}" for i in range(plugin_count)]

        for name in plugin_names:
            plugin = BlockableTestPlugin(name)
            plugins.append(plugin)
            await core.register_plugin(plugin)

        # Perform random block/unblock operations
        import random

        for _ in range(operation_count):
            plugin_name = random.choice(plugin_names)
            action = random.choice(["block", "unblock"])

            if action == "block":
                await core._registry.block(plugin_name)
                assert core._registry.is_blocked(plugin_name)
            else:
                await core._registry.unblock(plugin_name)
                assert not core._registry.is_blocked(plugin_name)

        await core.stop()

    @pytest.mark.asyncio
    @given(
        blocking_scenarios=st.lists(
            st.fixed_dictionaries(
                {
                    "plugin_name": st.text(
                        min_size=2, max_size=8, alphabet="abcdefghijklmnopqrstuvwxyz"
                    ),
                    "block_before_register": st.booleans(),
                    "block_after_register": st.booleans(),
                }
            ),
            min_size=1,
            max_size=3,
        )
    )
    async def test_blocking_timing_scenarios(self, blocking_scenarios):
        """Test different timing scenarios for blocking."""
        core = Core()
        await core.start()

        for index, scenario in enumerate(blocking_scenarios):
            # Suffix with the scenario index so repeated generated names cannot
            # collide with a plugin registered by an earlier scenario.
            plugin_name = f"{scenario['plugin_name']}_{index}"

            if scenario["block_before_register"]:
                # Block before registration
                await core._registry.block(plugin_name)
                assert core._registry.is_blocked(plugin_name)

            # Contract: blocked names are refused (False), others register (True).
            plugin = BlockableTestPlugin(plugin_name)
            expected = not scenario["block_before_register"]
            assert await core.register_plugin(plugin) is expected

            if scenario["block_after_register"] and not scenario["block_before_register"]:
                # Block after registration
                await core._registry.block(plugin_name)
                assert core._registry.is_blocked(plugin_name)

        await core.stop()

    @pytest.mark.asyncio
    @given(
        duplicate_operations=st.lists(
            st.fixed_dictionaries(
                {
                    "plugin_name": st.text(
                        min_size=2, max_size=8, alphabet="abcdefghijklmnopqrstuvwxyz"
                    ),
                    "action": st.one_of(st.just("block"), st.just("unblock")),
                }
            ),
            min_size=1,
            max_size=6,
        )
    )
    async def test_duplicate_block_operations(self, duplicate_operations):
        """Test duplicate block/unblock operations."""
        core = Core()

        for operation in duplicate_operations:
            plugin_name = operation["plugin_name"]
            action = operation["action"]

            initial_blocked_state = core._registry.is_blocked(plugin_name)

            if action == "block":
                await core._registry.block(plugin_name)
                # Should be blocked regardless of initial state
                assert core._registry.is_blocked(plugin_name)
            else:  # unblock
                result = await core._registry.unblock(plugin_name)
                # Should return True if it was blocked
                if initial_blocked_state:
                    assert result is True
                assert not core._registry.is_blocked(plugin_name)

    @pytest.mark.asyncio
    @given(
        special_names=st.lists(
            st.one_of(
                st.just(""),
                st.just(" "),
                st.text(min_size=1, max_size=5, alphabet="!@#$%^&*()"),
                st.text(max_size=0),  # empty string
            ),
            min_size=1,
            max_size=3,
        )
    )
    async def test_special_character_plugin_names(self, special_names):
        """Test blocking with special character plugin names."""
        core = Core()

        # The registry blocklist is a plain name set: any string (including
        # empty or punctuation-only names) blocks and unblocks cleanly.
        for name in special_names:
            await core._registry.block(name)
            assert core._registry.is_blocked(name)

            assert await core._registry.unblock(name) is True
            assert not core._registry.is_blocked(name)

    @pytest.mark.asyncio
    async def test_concurrent_blocking_operations(self, clean_core):
        """Test concurrent blocking operations."""
        import asyncio

        core = clean_core

        plugin_names = [f"concurrent_plugin_{i}" for i in range(10)]

        async def block_plugin(name):
            await core._registry.block(name)
            return core._registry.is_blocked(name)

        async def unblock_plugin(name):
            await core._registry.unblock(name)
            return not core._registry.is_blocked(name)

        # Run blocking operations concurrently
        block_tasks = [block_plugin(name) for name in plugin_names[:5]]
        block_results = await asyncio.gather(*block_tasks)

        # All block operations should succeed
        assert all(block_results)

        # Run unblocking operations concurrently
        unblock_tasks = [unblock_plugin(name) for name in plugin_names[:5]]
        unblock_results = await asyncio.gather(*unblock_tasks)

        # All unblock operations should succeed
        assert all(unblock_results)

    @pytest.mark.asyncio
    @given(
        blocking_sequences=st.lists(st.integers(min_value=1, max_value=5), min_size=1, max_size=5)
    )
    async def test_blocking_state_invariants(self, blocking_sequences):
        """Test that blocking maintains certain invariants."""
        core = Core()

        blocked_plugins = set()

        for i in blocking_sequences:
            plugin_name = f"invariant_plugin_{i % 3}"  # Cycle through 3 plugins

            # Block
            await core._registry.block(plugin_name)
            blocked_plugins.add(plugin_name)

            # Invariant: All blocked plugins should be reported as blocked
            for blocked_name in blocked_plugins:
                assert core._registry.is_blocked(blocked_name)

            # Occasionally unblock to test invariants during changes
            if i % 2 == 0 and blocked_plugins:
                unblock_name = next(iter(blocked_plugins))
                await core._registry.unblock(unblock_name)
                blocked_plugins.discard(unblock_name)

                # Invariant: Unblocked plugin should not be reported as blocked
                assert not core._registry.is_blocked(unblock_name)

    @pytest.mark.asyncio
    @given(
        plugin_lifecycles=st.lists(
            st.fixed_dictionaries(
                {
                    "block_at_start": st.booleans(),
                    "block_during": st.booleans(),
                    "block_at_end": st.booleans(),
                }
            ),
            min_size=1,
            max_size=3,
        )
    )
    async def test_blocking_throughout_lifecycle(self, plugin_lifecycles):
        """Test blocking behavior through plugin lifecycle."""
        core = Core()

        for i, lifecycle in enumerate(plugin_lifecycles):
            plugin_name = f"lifecycle_plugin_{i}"

            if lifecycle["block_at_start"]:
                await core._registry.block(plugin_name)

            await core.start()

            if lifecycle["block_during"] and not lifecycle["block_at_start"]:
                await core._registry.block(plugin_name)

            # Contract: registration is refused (False) iff the name is
            # blocked at registration time, and succeeds (True) otherwise.
            plugin = BlockableTestPlugin(plugin_name)
            expected = not (lifecycle["block_at_start"] or lifecycle["block_during"])
            assert await core.register_plugin(plugin) is expected

            await core.stop()

            if lifecycle["block_at_end"]:
                await core._registry.block(plugin_name)

            # Final state check
            final_blocked = (
                lifecycle["block_at_start"]
                or lifecycle["block_during"]
                or lifecycle["block_at_end"]
            )

            assert core._registry.is_blocked(plugin_name) == final_blocked

    @pytest.mark.asyncio
    @given(
        blocked_configs=st.sets(
            st.text(min_size=2, max_size=8, alphabet="abcdefghijklmnopqrstuvwxyz"),
            min_size=0,
            max_size=3,
        )
    )
    async def test_config_blocking_with_runtime_blocking(self, blocked_configs):
        """Test interaction between config blocking and runtime blocking."""
        # Start with config-specified blocked plugins
        core = Core(blocked_plugins=frozenset(blocked_configs))

        # All config plugins should be blocked
        for name in blocked_configs:
            assert core._registry.is_blocked(name)

        # Add runtime blocking
        runtime_blocked = {"runtime_block_1", "runtime_block_2"}
        for name in runtime_blocked:
            await core._registry.block(name)

        # Both config and runtime blocks should be in effect
        for name in blocked_configs.union(runtime_blocked):
            assert core._registry.is_blocked(name)

        # Runtime unblocking should work even for config-blocked plugins
        # (depending on implementation)
        if blocked_configs:
            config_plugin = next(iter(blocked_configs))
            was_blocked = await core._registry.unblock(config_plugin)
            # Implementation might prevent unblocking config-specified blocks
            # This test documents current behavior
