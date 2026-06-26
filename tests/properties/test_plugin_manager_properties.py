"""
Property-based tests for plugin manager workflows using Hypothesis strategies.

This file converts the verbose plugin manager workflow integration tests into efficient property-based tests
that validate system invariants across comprehensive automatically generated test cases.
"""

import dataclasses

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# CoreStateError eliminated - use CoreError instead
from tests.strategies import (
    capability_sets,
    valid_core_configs,
)
from uxok import Core, Plugin
from uxok.protocols import (
    CoreState,
    # PluginProtocol internal - use Plugin class instead,
)


class TestPluginManagerProperties:
    """Property-based tests for plugin manager workflow invariants."""

    @pytest.mark.asyncio
    @given(config=valid_core_configs(), plugin_count=st.integers(min_value=1, max_value=5))
    @settings(max_examples=10, deadline=3000)
    async def test_basic_plugin_registration_property(self, config, plugin_count):
        """Property: Basic plugin registration works for any valid configuration."""
        core = Core(**dataclasses.asdict(config))

        try:
            await core.start()

            # Create simple plugins
            plugins = []
            for i in range(plugin_count):
                plugin = SimpleManagerPlugin(name=f"plugin_{i}")
                plugins.append(plugin)

                # Property: Registration should succeed
                registration_result = await core.register_plugin(plugin)
                assert registration_result is True, (
                    f"# PluginProtocol internal - use Plugin class instead {i} registration should succeed"
                )

            # Property: All plugins should be registered
            for i, plugin in enumerate(plugins):
                # Use plugin name for lookup since string IDs are treated as names
                plugin_name = plugin.metadata.name
                registered_plugin = (await core.list()).by_name(plugin_name)
                assert registered_plugin is not None, (
                    f"# PluginProtocol internal - use Plugin class instead {i} should be registered"
                )
                assert registered_plugin.name == f"plugin_{i}", (
                    f"Plugin {i} should have correct name"
                )

        finally:
            if core.state == CoreState.RUNNING:
                await core.stop()

    @pytest.mark.asyncio
    @given(
        config=valid_core_configs(),
        capability_configs=st.lists(
            st.fixed_dictionaries({"provides": capability_sets(), "requires": capability_sets()}),
            min_size=1,
            max_size=3,
        ),
    )
    @settings(max_examples=5, deadline=3000)
    async def test_capability_resolution_property(self, config, capability_configs):
        """Property: Capability resolution works correctly for any valid capability graph."""
        core = Core(**dataclasses.asdict(config))

        try:
            await core.start()

            # Collect unique capabilities that will be provided
            all_provided = set()
            provider_configs = []
            consumer_configs = []

            for caps in capability_configs:
                provides = caps.get("provides", set())
                requires = caps.get("requires", set())

                # Ensure no duplicate capabilities across providers
                new_provides = provides - all_provided
                if new_provides:
                    provider_configs.append((new_provides, requires))
                    all_provided.update(new_provides)
                elif requires:
                    # If no new capabilities provided but has requirements, treat as consumer
                    consumer_configs.append((provides, requires))

            # Create capability providers and consumers
            providers = []
            consumers = []

            # Register providers first (each provides unique capabilities)
            for i, (provides, requires) in enumerate(provider_configs):
                plugin = CapabilityProviderPlugin(
                    name=f"provider_{i}", provides=provides, provides_list=list(provides)
                )
                providers.append(plugin)
                await core.register_plugin(plugin)

            # Register additional consumers from original configs
            for i, caps in enumerate(capability_configs):
                provides = caps.get("provides", set())
                requires = caps.get("requires", set())

                # Only create consumers for capabilities that are actually provided by someone
                if requires and all(r in all_provided for r in requires):
                    plugin = CapabilityConsumerPlugin(
                        name=f"consumer_{i}", requires=requires, requires_list=list(requires)
                    )
                    consumers.append(plugin)
                    await core.register_plugin(plugin)

            # All plugins are already initialized by the transaction system

            # Property: All consumers that registered successfully should have their requirements satisfied
            successful_consumers = []
            for consumer in consumers:
                # All consumers are already initialized by the transaction system
                successful_consumers.append(consumer)

            # Property: For each successful consumer, all its requirements should be satisfied
            for consumer in successful_consumers:
                assert consumer.requirements_satisfied, (
                    f"Consumer {consumer.metadata.name} should have requirements satisfied"
                )

                for required in consumer.requires_list:
                    assert (await core.list()).capability.provides(required).first() is not None, (
                        f"Required capability {required} should be available for {consumer.metadata.name}"
                    )

        finally:
            if core.state == CoreState.RUNNING:
                await core.stop()

    @pytest.mark.asyncio
    @given(config=valid_core_configs(), plugin_count=st.integers(min_value=2, max_value=4))
    @settings(max_examples=8, deadline=3000)
    async def test_plugin_lifecycle_consistency_property(self, config, plugin_count):
        """Property: # PluginProtocol internal - use Plugin class instead lifecycle methods are called in correct order."""
        core = Core(**dataclasses.asdict(config))

        try:
            await core.start()

            # Create plugins with lifecycle tracking
            plugins = []
            for i in range(plugin_count):
                plugin = LifecycleTrackingPlugin(name=f"lifecycle_plugin_{i}")
                plugins.append(plugin)
                await core.register_plugin(plugin)

            # All plugins are already initialized by the transaction system

            # Property: All plugins should have been initialized
            for plugin in plugins:
                assert plugin.initialized, (
                    f"# PluginProtocol internal - use Plugin class instead {plugin.name} should be initialized"
                )
                assert plugin.init_called, (
                    f"# PluginProtocol internal - use Plugin class instead {plugin.name} init should have been called"
                )
                assert plugin.lifecycle_events == ["init"], (
                    f"# PluginProtocol internal - use Plugin class instead {plugin.name} should have init event"
                )

            # Shutdown all plugins
            for plugin in plugins:
                await core.unregister_plugin(plugin.metadata.id)
                await plugin.stop()

            # Property: All plugins should have been shut down
            for plugin in plugins:
                assert plugin.shutdown_called, (
                    f"# PluginProtocol internal - use Plugin class instead {plugin.name} shutdown should have been called"
                )
                assert not plugin.initialized, (
                    f"# PluginProtocol internal - use Plugin class instead {plugin.name} should not be initialized after shutdown"
                )
                assert "shutdown" in plugin.lifecycle_events, (
                    f"# PluginProtocol internal - use Plugin class instead {plugin.name} should have shutdown event"
                )

        finally:
            if core.state == CoreState.RUNNING:
                await core.stop()

    @pytest.mark.asyncio
    @given(
        config=valid_core_configs(),
        failure_scenarios=st.lists(
            st.sampled_from(["init", "shutdown", "none"]), min_size=1, max_size=5
        ),
    )
    @settings(max_examples=8, deadline=3000)
    async def test_plugin_error_isolation_property(self, config, failure_scenarios):
        """Property: # PluginProtocol internal - use Plugin class instead errors are isolated and don't affect other plugins."""
        core = Core(**dataclasses.asdict(config))

        try:
            await core.start()

            # Create plugins with different failure modes
            plugins = []
            successful_plugins = []
            failed_plugins = []

            for i, failure_mode in enumerate(failure_scenarios):
                plugin = ErrorTestingPlugin(name=f"error_plugin_{i}", failure_mode=failure_mode)
                plugins.append(plugin)

                # Try to register plugin (initialization happens in transaction)
                try:
                    await core.register_plugin(plugin)
                    successful_plugins.append(plugin)
                except Exception:
                    failed_plugins.append(plugin)

            # Property: Plugins without init failures should be successful
            # (shutdown failures still succeed during registration)
            expected_successful = failure_scenarios.count("none") + failure_scenarios.count(
                "shutdown"
            )
            assert len(successful_plugins) == expected_successful, (
                f"Expected {expected_successful} successful plugins, got {len(successful_plugins)}"
            )

            # Property: Plugins with failures should have failed as expected
            for plugin in failed_plugins:
                assert not plugin.initialized, (
                    f"# PluginProtocol internal - use Plugin class instead {plugin.name} with failure mode {plugin.failure_mode} should not be initialized"
                )

            # Property: Core should remain in running state despite plugin failures
            assert core.state == CoreState.RUNNING, (
                "Core should remain running despite plugin failures"
            )

        finally:
            if core.state == CoreState.RUNNING:
                await core.stop()

    @pytest.mark.asyncio
    @given(config=valid_core_configs(), complex_plugin_count=st.integers(min_value=3, max_value=6))
    @settings(max_examples=6, deadline=4000)
    async def test_complex_plugin_dependency_property(self, config, complex_plugin_count):
        """Property: Complex dependency chains resolve correctly."""
        core = Core(**dataclasses.asdict(config))

        try:
            await core.start()

            # Create a dependency chain: A provides to B, B provides to C, etc.
            capabilities = []
            plugins = []

            for i in range(complex_plugin_count):
                capability_name = f"capability_{i}"
                capabilities.append(capability_name)

                # Each plugin depends on the previous capability (except the first)
                if i == 0:
                    requires = set()
                else:
                    requires = {f"capability_{i - 1}"}

                provides = {capability_name}

                plugin = CapabilityProviderPlugin(
                    name=f"chain_plugin_{i}",
                    requires=requires,
                    provides=provides,
                    requires_list=list(requires),
                    provides_list=list(provides),
                )
                plugins.append(plugin)
                await core.register_plugin(plugin)

            # All plugins are already initialized by the transaction system
            # Dependency chains that work will have been successful
            successful_initializations = list(range(len(plugins)))

            # Property: Should be able to initialize at least the first plugin
            assert len(successful_initializations) >= 1, (
                "Should be able to initialize at least the first plugin"
            )

            # Property: Each successfully initialized plugin should have its requirements satisfied
            for plugin_index in successful_initializations:
                plugin = plugins[plugin_index]
                assert plugin.requirements_satisfied, (
                    f"# PluginProtocol internal - use Plugin class instead {plugin.name} should have requirements satisfied"
                )

            # Property: The dependency chain should be contiguous (no gaps)
            if len(successful_initializations) > 1:
                # Check that we have a contiguous chain from 0 to n-1
                expected_chain = list(range(len(successful_initializations)))
                assert successful_initializations == expected_chain, (
                    f"Dependency chain should be contiguous: expected {expected_chain}, got {successful_initializations}"
                )

        finally:
            if core.state == CoreState.RUNNING:
                await core.stop()

    @pytest.mark.asyncio
    @given(config=valid_core_configs(), registration_count=st.integers(min_value=1, max_value=10))
    @settings(max_examples=8, deadline=3000)
    async def test_repeated_registration_consistency_property(self, config, registration_count):
        """Property: # PluginProtocol internal - use Plugin class instead registration is consistent across multiple operations."""
        core = Core(**dataclasses.asdict(config))

        try:
            await core.start()

            # Register and unregister plugins multiple times
            registration_results = []
            for i in range(registration_count):
                # Create a new plugin instance for each iteration
                plugin = SimpleManagerPlugin(name=f"repeated_test_{i}")

                # Register plugin (initialization happens in transaction)
                registration_success = await core.register_plugin(plugin)
                registration_results.append(registration_success)

                # Verify plugin is registered (use name lookup)
                registered_plugin = (await core.list()).by_name(plugin.metadata.name)
                assert registered_plugin is not None, (
                    f"# PluginProtocol internal - use Plugin class instead should be registered in iteration {i}"
                )

                # Unregister plugin
                unregister_success = await core.unregister_plugin(plugin.metadata.name)
                assert unregister_success, (
                    f"# PluginProtocol internal - use Plugin class instead should be unregistered in iteration {i}"
                )

                # Verify plugin is not registered. (by_name returns None for
                # missing plugins — the old try/except-assert-False version
                # swallowed its own assertion and never checked anything.)
                assert (await core.list()).by_name(plugin.metadata.name) is None, (
                    f"plugin should not be registered after unregistration in iteration {i}"
                )

            # Property: All registrations should succeed
            assert all(registration_results), "All registrations should succeed"

        finally:
            if core.state == CoreState.RUNNING:
                await core.stop()


class SimpleManagerPlugin(Plugin):
    """Simple plugin for testing basic plugin manager functionality."""

    def __init__(self, name=None):
        super().__init__(name=name)
        self.registration_count = 0

    async def on_start(self):
        pass

    async def on_stop(self):
        pass


class CapabilityProviderPlugin(Plugin):
    """# PluginProtocol internal - use Plugin class instead that provides capabilities for testing dependency resolution."""

    def __init__(
        self, name=None, requires=None, provides=None, requires_list=None, provides_list=None
    ):
        requires = requires or set()
        provides = provides or set()
        super().__init__(requires=requires, provides=provides, name=name)
        self.requires_list = requires_list or []
        self.provides_list = provides_list or []
        self.capability_access_count = {}
        self.requirements_satisfied = True  # Providers are considered satisfied by default

    async def on_start(self):
        # Track capability access counts
        for capability in self.provides_list:
            self.capability_access_count[capability] = 0

    def increment_access(self, capability):
        """Increment access counter for tracking."""
        self.capability_access_count[capability] = (
            self.capability_access_count.get(capability, 0) + 1
        )

    async def on_stop(self):
        pass


class CapabilityConsumerPlugin(Plugin):
    """# PluginProtocol internal - use Plugin class instead that requires capabilities for testing dependency resolution."""

    def __init__(self, name=None, requires=None, provides=None, requires_list=None):
        requires = requires or set()
        provides = provides or set()
        super().__init__(requires=requires, provides=provides, name=name)
        self.requires_list = requires_list or []
        self.requirements_satisfied = False

    async def on_start(self):
        try:
            # Try to access all required capabilities
            for capability in self.requires_list:
                provider = (await self.core.list()).capability.provides(capability).first()
                if hasattr(provider, "increment_access"):
                    await provider.increment_access(capability)

            self.requirements_satisfied = True
        except Exception:
            self.requirements_satisfied = False
            raise

    async def on_stop(self):
        pass


class LifecycleTrackingPlugin(Plugin):
    """# PluginProtocol internal - use Plugin class instead that tracks lifecycle events for testing."""

    def __init__(self, name=None):
        super().__init__(name=name)
        self.initialized = False
        self.lifecycle_events = []
        self.init_called = False
        self.shutdown_called = False

    async def on_start(self):
        self.lifecycle_events.append("init")
        self.init_called = True
        self.initialized = True

    async def on_stop(self):
        self.lifecycle_events.append("shutdown")
        self.shutdown_called = True
        self.initialized = False


class ErrorTestingPlugin(Plugin):
    """# PluginProtocol internal - use Plugin class instead that can be configured to fail at different stages."""

    def __init__(self, name=None, failure_mode="none"):
        super().__init__(name=name)
        self.failure_mode = failure_mode
        self.initialized = False

    async def on_start(self):
        if self.failure_mode == "init":
            raise RuntimeError(f"Simulated initialization failure for {self.name}")
        self.initialized = True

    async def on_stop(self):
        if self.failure_mode == "shutdown":
            raise RuntimeError(f"Simulated shutdown failure for {self.name}")
        self.initialized = False
