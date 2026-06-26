"""
Plugin System Property-Based Tests.

This module tests the plugin system using property-based testing to ensure
comprehensive coverage of plugin registration, lifecycle management, capability
resolution, and error handling through focused property definitions.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import run_state_machine_as_test

from tests.helpers import CapabilityTestPlugin
from tests.state_machines import PluginLifecycleStateMachine
from tests.strategies import (
    capability_sets,
    plugin_capability_combinations,
)
from uxok import Core, Plugin
from uxok.errors import MissingCapabilityError, PluginError
from uxok.protocols import (
    CoreState,
    PluginMetadata,
)


class TestPluginSystemProperties:
    """Property-based tests for plugin system invariants."""

    @pytest.mark.asyncio
    @given(plugin_configs=st.lists(plugin_capability_combinations(), min_size=1, max_size=10))
    @settings(max_examples=25, deadline=3000)
    async def test_plugin_dependency_acyclic_graph(self, plugin_configs):
        """
        Property: registration succeeds exactly when every required capability
        is already provided, and the resulting dependency graph has no cycles.
        """
        core = Core()
        await core.start()

        try:
            plugins = []
            registration_results = []
            available_capabilities: set[str] = set()

            for i, (provides, requires) in enumerate(plugin_configs):
                plugin = CapabilityTestPlugin(
                    name=f"plugin_{i}", provides=provides, requires=requires
                )
                plugins.append(plugin)

                if requires <= available_capabilities:
                    # Contract: requirements satisfied -> registration succeeds.
                    assert await core.register_plugin(plugin) is True
                    available_capabilities |= provides
                    registration_results.append(True)
                else:
                    # Contract: missing requirement -> registration is rejected.
                    with pytest.raises(MissingCapabilityError):
                        await core.register_plugin(plugin)
                    registration_results.append(False)

            # Invariant: dependency graph of registered plugins is acyclic.
            assert not _has_cycles(_dependency_edges(plugins, registration_results))

        finally:
            await core.stop()

    @pytest.mark.asyncio
    @given(capabilities=capability_sets().filter(lambda s: len(s) >= 1))
    @settings(max_examples=25, deadline=2000)
    async def test_capability_uniqueness_enforcement(self, capabilities):
        """
        Property: under the ``error_on_conflict`` collision policy each
        capability can only be provided by one plugin, and a rejected
        duplicate leaves the original provider in place.
        """
        core = Core(capability_collision="error_on_conflict")
        await core.start()

        try:
            provider1 = CapabilityTestPlugin(name="provider1", provides=capabilities)
            provider2 = CapabilityTestPlugin(name="provider2", provides=capabilities)

            # First provider should succeed
            assert await core.register_plugin(provider1) is True

            # Second provider should be rejected before any mutation
            with pytest.raises(PluginError, match="Capability"):
                await core.register_plugin(provider2)

            # Verify the first provider is still the provider of record
            for cap in capabilities:
                proxy = (await core.list()).capability.provides(cap).first()
                assert proxy is not None
                assert proxy.name == provider1.metadata.name
                assert await core.get_capability(cap) is provider1

        finally:
            await core.stop()

    @pytest.mark.asyncio
    @given(
        provider_count=st.integers(min_value=1, max_value=4),
        consumer_count=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=20, deadline=3000)
    async def test_capability_resolution_correctness(self, provider_count, consumer_count):
        """
        Property: Capability resolution connects consumers to correct providers.
        """
        core = Core()
        await core.start()

        try:
            providers = []
            consumers = []

            # Create capability providers (one base capability each)
            base_capabilities = ["database", "cache", "queue", "logging"]
            for i in range(min(provider_count, len(base_capabilities))):
                provider = CapabilityTestPlugin(
                    name=f"provider_{i}", provides={base_capabilities[i]}
                )
                providers.append(provider)
                assert await core.register_plugin(provider) is True

            available_caps: set[str] = set()
            for provider in providers:
                available_caps.update(provider.metadata.provides)

            # Create capability consumers: each requires up to 2 capabilities,
            # selected deterministically from those available.
            for i in range(consumer_count):
                required_caps = set(sorted(available_caps)[: min(2, len(available_caps))])
                consumer = CapabilityTestPlugin(name=f"consumer_{i}", requires=required_caps)
                consumers.append(consumer)
                # All requirements are provided, so registration must succeed.
                assert await core.register_plugin(consumer) is True

            # Verify all consumers resolve to a provider of each required capability
            for consumer in consumers:
                for capability in consumer.metadata.requires:
                    provider = await core.get_capability(capability)
                    assert provider is not None
                    assert capability in provider.metadata.provides

        finally:
            await core.stop()

    @pytest.mark.asyncio
    @given(
        operation_sequence=st.lists(
            st.sampled_from(
                [
                    "register_provider",
                    "register_consumer",
                    "unregister",
                    "register_invalid",
                    "duplicate_register",
                ]
            ),
            min_size=10,
            max_size=50,
        )
    )
    @settings(max_examples=15, deadline=5000)
    async def test_plugin_lifecycle_state_consistency(self, operation_sequence):
        """
        Property: plugin lifecycle state transitions stay consistent across
        complex operation sequences, and the core never leaves RUNNING.
        """
        core = Core()
        await core.start()

        try:
            plugins = {}
            plugin_states = {}
            plugin_counter = 0

            def registered_names():
                return [n for n, s in plugin_states.items() if s == "registered"]

            for operation in operation_sequence:
                if operation == "register_provider":
                    plugin = CapabilityTestPlugin(
                        name=f"provider_{plugin_counter}", provides={"test_capability"}
                    )
                    plugins[plugin.metadata.name] = plugin

                    assert await core.register_plugin(plugin) is True
                    plugin_states[plugin.metadata.name] = "registered"
                    plugin_counter += 1

                elif operation == "register_consumer":
                    provider_live = any(
                        "test_capability" in plugins[n].metadata.provides
                        for n in registered_names()
                    )
                    if provider_live:
                        consumer = CapabilityTestPlugin(
                            name=f"consumer_{plugin_counter}", requires={"test_capability"}
                        )
                        plugins[consumer.metadata.name] = consumer

                        assert await core.register_plugin(consumer) is True
                        plugin_states[consumer.metadata.name] = "registered"
                        plugin_counter += 1

                elif operation == "unregister":
                    names = registered_names()
                    if names:
                        plugin_name = names[plugin_counter % len(names)]
                        plugin = plugins[plugin_name]

                        dependents = await core._registry.dependents(plugin.metadata.id)
                        has_active_dependents = any(
                            [await core._registry.contains(d) for d in dependents]
                        )
                        if has_active_dependents:
                            # Contract: unregistering a plugin with live
                            # dependents is refused (use force=True to override).
                            with pytest.raises(PluginError, match="dependents present"):
                                await core.unregister_plugin(plugin.metadata.id)
                        else:
                            assert await core.unregister_plugin(plugin.metadata.id) is True
                            plugin_states[plugin_name] = "unregistered"
                        plugin_counter += 1

                elif operation == "register_invalid":
                    # Invalid metadata (empty name/version) is rejected at
                    # construction time by PluginMetadata.__post_init__.
                    with pytest.raises(ValueError, match="cannot be empty"):
                        InvalidTestPlugin(f"invalid_{plugin_counter}")
                    plugin_counter += 1

                elif operation == "duplicate_register":
                    names = registered_names()
                    if names:
                        plugin = plugins[names[plugin_counter % len(names)]]

                        # Registering the same (still registered) plugin twice fails.
                        with pytest.raises(PluginError, match="already registered"):
                            await core.register_plugin(plugin)
                        plugin_counter += 1

            # Invariant: core state remains RUNNING throughout
            assert core.state is CoreState.RUNNING

            # Invariant: all registered plugins are retrievable
            collection = await core.list()
            for plugin_name, state in plugin_states.items():
                if state == "registered":
                    assert collection.by_id(plugins[plugin_name].metadata.id) is not None

        finally:
            await core.stop()

    @pytest.mark.asyncio
    @given(
        plugin_count=st.integers(min_value=5, max_value=30),
        failure_rate=st.floats(min_value=0.0, max_value=0.3, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=10, deadline=5000)
    async def test_plugin_system_resilience(self, plugin_count, failure_rate):
        """
        Property: the plugin system remains stable under partial failures —
        a plugin that raises in on_start is rolled back, everything else lands.
        """
        core = Core()
        await core.start()

        try:
            good_plugins = []
            failure_count = int(plugin_count * failure_rate)
            failure_indices = set(range(failure_count))

            for i in range(plugin_count):
                if i in failure_indices:
                    # A plugin raising in on_start fails registration with the
                    # original error, after rollback.
                    failing = FailingTestPlugin(f"failing_{i}")
                    with pytest.raises(RuntimeError, match="initialization failure"):
                        await core.register_plugin(failing)
                else:
                    plugin = CapabilityTestPlugin(
                        name=f"successful_{i}", provides={f"capability_{i}"}
                    )
                    assert await core.register_plugin(plugin) is True
                    good_plugins.append(plugin)

            # Invariant: Core remains stable
            assert core.state is CoreState.RUNNING

            # Invariant: exactly the non-failing plugins are registered
            collection = await core.list()
            assert collection.count == plugin_count - failure_count
            for plugin in good_plugins:
                assert collection.by_id(plugin.metadata.id) is not None

        finally:
            await core.stop()

    @pytest.mark.asyncio
    @given(
        capability_count=st.integers(min_value=1, max_value=15),
        dependency_depth=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=10, deadline=3000)
    async def test_complex_dependency_resolution(self, capability_count, dependency_depth):
        """
        Property: dependency chains resolve correctly when registered in
        dependency order, and a missing requirement is rejected at
        registration time (registration order matters by design).
        """
        core = Core()
        await core.start()

        try:
            plugins = []
            for i in range(capability_count):
                requires = {f"capability_{max(0, i - dependency_depth)}"} if i > 0 else set()
                plugin = CapabilityTestPlugin(
                    name=f"chain_provider_{i}",
                    provides={f"capability_{i}"},
                    requires=requires,
                )
                plugins.append(plugin)
                assert await core.register_plugin(plugin) is True

            # Every capability resolves to its (sole) provider.
            for i, plugin in enumerate(plugins):
                assert await core.get_capability(f"capability_{i}") is plugin

            # The dependency graph is acyclic.
            assert not _has_cycles(_dependency_edges(plugins, [True] * len(plugins)))

            # Contract: requirements are validated at registration time, so a
            # consumer of an unprovided capability is rejected outright.
            orphan = CapabilityTestPlugin(
                name="orphan_consumer", requires={"capability_unprovided"}
            )
            with pytest.raises(MissingCapabilityError):
                await core.register_plugin(orphan)

        finally:
            await core.stop()


class InvalidTestPlugin(Plugin):
    """Plugin whose construction installs invalid (empty) metadata.

    ``PluginMetadata.__post_init__`` rejects empty name/version, so
    instantiating this class raises ValueError.
    """

    def __init__(self, name=None):
        super().__init__(name=name)
        # Invalid metadata: raises ValueError at construction.
        self._metadata = PluginMetadata(
            name="",  # Invalid: empty name
            version="",  # Invalid: empty version
            id=self._metadata.id,
        )


class FailingTestPlugin(Plugin):
    """Plugin that fails during initialization for testing resilience."""

    def __init__(self, name=None):
        super().__init__(name=name)
        self.failure_count = 0

    async def on_start(self):
        self.failure_count += 1
        raise RuntimeError(
            f"Plugin {self.metadata.name} initialization failure #{self.failure_count}"
        )


def _dependency_edges(plugins, registration_results):
    """Reconstruct the kernel's dependency edges for registered plugins.

    The kernel records, for each required capability, an edge to the provider
    of record at registration time (``capability_selection="last_registered"``
    selects the most recently registered provider). Returns a mapping
    consumer name -> set of provider names.
    """
    providers_by_cap: dict[str, list[str]] = {}
    edges: dict[str, set[str]] = {}
    for plugin, success in zip(plugins, registration_results):
        if not success:
            continue
        name = plugin.metadata.name
        edges[name] = {
            providers_by_cap[req][-1] for req in plugin.metadata.requires if req in providers_by_cap
        }
        # Provides become visible only after the edges are computed, so a
        # plugin never records a dependency edge to itself.
        for cap in plugin.metadata.provides:
            providers_by_cap.setdefault(cap, []).append(name)
    return edges


def _has_cycles(edges):
    """DFS cycle check over a consumer -> providers edge mapping."""
    visited: set[str] = set()
    rec_stack: set[str] = set()

    def dfs(node):
        visited.add(node)
        rec_stack.add(node)
        for nxt in edges.get(node, ()):
            if nxt in rec_stack:
                return True
            if nxt not in visited and dfs(nxt):
                return True
        rec_stack.discard(node)
        return False

    return any(node not in visited and dfs(node) for node in edges)


# =============================================================================
# STATE MACHINE TESTS
# =============================================================================


def test_plugin_lifecycle_via_state_machine():
    """Test plugin lifecycle using state machine exploration."""
    run_state_machine_as_test(PluginLifecycleStateMachine)


if __name__ == "__main__":
    # Run individual property tests
    pytest.main([__file__, "-v"])
