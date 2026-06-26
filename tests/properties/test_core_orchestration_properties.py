"""
Property-based tests for core orchestration using Hypothesis strategies.

This file converts the verbose integration test scenarios into efficient property-based tests
that validate system invariants across comprehensive automatically generated test cases.
"""

import dataclasses

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.strategies import (
    edge_case_configs,
    valid_core_configs,
)
from uxok import Core, Plugin
from uxok.protocols import (
    CoreConfig,
    CoreState,
)


class TestCoreOrchestrationProperties:
    """Property-based tests for core orchestration invariants."""

    @pytest.mark.asyncio
    @given(config=valid_core_configs(), num_plugins=st.integers(min_value=1, max_value=5))
    @settings(max_examples=15, deadline=5000)
    async def test_complex_dependency_resolution_property(self, config, num_plugins):
        """Property: provider/consumer dependency graphs resolve for any valid configuration."""
        core = Core(**dataclasses.asdict(config))

        try:
            await core.start()

            # Create simple capability provider first
            provider = OrchestrationTestPlugin(
                name="provider", provides={"test_capability"}, requires=set()
            )
            assert await core.register_plugin(provider) is True

            # Create consumers; the provider exists and valid_core_configs
            # guarantees max_plugins headroom, so every registration succeeds.
            consumers = []
            for i in range(num_plugins):
                consumer = OrchestrationTestPlugin(
                    name=f"consumer_{i}", provides=set(), requires={"test_capability"}
                )
                assert await core.register_plugin(consumer) is True
                consumers.append(consumer)

            # Property: every registered plugin is initialized
            assert provider.initialized, "Provider should be initialized"
            assert len(consumers) == num_plugins
            for consumer in consumers:
                assert consumer.initialized, f"{consumer.metadata.name} should be initialized"

        finally:
            if core.state == CoreState.RUNNING:
                await core.stop()

    @pytest.mark.asyncio
    @given(config=valid_core_configs(), num_plugins=st.integers(min_value=1, max_value=3))
    @settings(max_examples=10, deadline=3000)
    async def test_plugin_lifecycle_consistency_property(self, config, num_plugins):
        """Property: plugin lifecycle behaves consistently for any valid plugin set."""
        core = Core(**dataclasses.asdict(config))

        try:
            await core.start()

            # Create simple plugins — dependency-free, so all registrations succeed.
            plugins = []
            for i in range(num_plugins):
                plugin = OrchestrationTestPlugin(
                    name=f"lifecycle_plugin_{i}", provides=set(), requires=set()
                )
                assert await core.register_plugin(plugin) is True
                plugins.append(plugin)

            # Property: every plugin initialized
            assert len(plugins) == num_plugins
            for plugin in plugins:
                assert plugin.initialized, f"{plugin.metadata.name} should be initialized"

            # Property: Core should remain in running state with successful plugins
            assert core.state == CoreState.RUNNING, (
                "Core should remain running with successful plugins"
            )

            # Stop core and verify final state
            await core.stop()

            # Property: Core should be stopped after shutdown
            assert core.state == CoreState.STOPPED, "Core should be stopped after shutdown"

        finally:
            if core.state == CoreState.RUNNING:
                await core.stop()

    @pytest.mark.asyncio
    @given(
        num_capabilities=st.integers(min_value=1, max_value=3),
        num_consumers=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=12, deadline=4000)
    async def test_capability_isolation_property(self, num_capabilities, num_consumers):
        """Property: Capability system maintains isolation between different capabilities."""
        core = Core()

        try:
            await core.start()

            # Create capability providers
            capability_names = [f"capability_{i}" for i in range(num_capabilities)]
            provider_plugins = {}

            for cap_name in capability_names:
                provider = ProviderConsumerPlugin(
                    {cap_name}, name=f"provider_{cap_name}", provider=True
                )
                provider_plugins[cap_name] = provider
                assert await core.register_plugin(provider) is True

            # Create consumers — each requires exactly one existing capability,
            # so every registration succeeds and gains capability access.
            consumer_plugins = []
            for i in range(num_consumers):
                cap_index = i % num_capabilities
                required_cap = capability_names[cap_index]

                consumer = ProviderConsumerPlugin(
                    {required_cap}, name=f"consumer_{i}", provider=False
                )
                assert await core.register_plugin(consumer) is True
                consumer_plugins.append(consumer)

                assert consumer.initialized
                assert consumer.capability_granted, f"Consumer {i} should have capability access"

            # Property: each provider was accessed exactly by its own consumers
            # (capability isolation: no cross-capability access).
            for j, cap_name in enumerate(capability_names):
                expected_accesses = sum(
                    1 for i in range(num_consumers) if i % num_capabilities == j
                )
                provider = provider_plugins[cap_name]
                assert provider.access_count.get(cap_name, 0) == expected_accesses
                # No accesses recorded for capabilities this provider doesn't own.
                assert set(provider.access_count) <= {cap_name}

        finally:
            if core.state == CoreState.RUNNING:
                await core.stop()

    @pytest.mark.asyncio
    @given(
        num_plugins=st.integers(min_value=1, max_value=5),
        concurrent_factor=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=15, deadline=3000)
    async def test_concurrent_registration_safety_property(self, num_plugins, concurrent_factor):
        """Property: Concurrent plugin registration maintains system consistency."""
        import asyncio
        from uuid import uuid4

        core = Core(max_plugins=num_plugins * 2)

        try:
            await core.start()

            # Create shared provider
            shared_capability = f"shared_capability_{uuid4().hex[:8]}"
            provider = ProviderConsumerPlugin(
                {shared_capability}, name="shared_provider", provider=True
            )
            assert await core.register_plugin(provider) is True

            # Create consumers concurrently
            async def create_consumer(index):
                consumer = ProviderConsumerPlugin(
                    {shared_capability}, name=f"consumer_{index}", provider=False
                )
                assert await core.register_plugin(consumer) is True
                return consumer

            # Run concurrent registrations; the provider exists and
            # max_plugins has headroom, so every registration must succeed.
            batch_size = min(concurrent_factor, num_plugins)
            all_consumers = []

            for batch_start in range(0, num_plugins, batch_size):
                batch_end = min(batch_start + batch_size, num_plugins)
                batch_consumers = await asyncio.gather(
                    *[create_consumer(i) for i in range(batch_start, batch_end)]
                )
                all_consumers.extend(batch_consumers)

            # Property: every consumer registered and gained capability access
            assert len(all_consumers) == num_plugins
            assert all(consumer.capability_granted for consumer in all_consumers)

            # Property: the provider recorded exactly one access per consumer
            assert provider.access_count.get(shared_capability, 0) == num_plugins

        finally:
            if core.state == CoreState.RUNNING:
                await core.stop()

    @pytest.mark.asyncio
    @given(config=edge_case_configs())
    @settings(max_examples=10, deadline=4000)
    async def test_edge_case_configuration_property(self, config):
        """Property: System handles edge case configurations gracefully."""
        core = Core(**dataclasses.asdict(config))

        try:
            await core.start()

            # Property: a successful start always lands in RUNNING
            assert core.state == CoreState.RUNNING, f"Invalid core state after start: {core.state}"

            # Edge-case configs are still valid configs (max_plugins >= 10),
            # so a basic plugin registers and initializes without error.
            plugin = OrchestrationTestPlugin(name="config_test")
            assert await core.register_plugin(plugin) is True
            assert plugin.initialized, "Plugin should initialize with edge case config"

        finally:
            if core.state == CoreState.RUNNING:
                await core.stop()

    @pytest.mark.asyncio
    @given(
        max_plugins=st.integers(min_value=1, max_value=20),
    )
    @settings(max_examples=12, deadline=3000)
    async def test_configuration_validation_property(self, max_plugins):
        """Property: Valid configurations work correctly."""
        # Valid configuration must construct and start without raising.
        CoreConfig(max_plugins=max_plugins)
        core = Core(max_plugins=max_plugins)
        await core.start()

        # Property: Valid config should allow core start
        assert core.state == CoreState.RUNNING, "Valid config should result in running state"

        await core.stop()
        assert core.state == CoreState.STOPPED

    @pytest.mark.asyncio
    @given(num_consumers=st.integers(min_value=1, max_value=5))
    @settings(max_examples=8, deadline=5000)
    async def test_registration_order_independence_property(self, num_consumers):
        """Property: every consumer gets capability access once its provider exists."""
        core = Core(max_plugins=10)

        try:
            await core.start()

            # Create capability provider
            provider = ProviderConsumerPlugin({"test_capability"}, name="provider", provider=True)
            assert await core.register_plugin(provider) is True

            # Create consumers — provider exists and max_plugins=10 has
            # headroom for provider + 5 consumers, so all must succeed.
            consumers = []
            for i in range(num_consumers):
                consumer = ProviderConsumerPlugin(
                    {"test_capability"}, name=f"consumer_{i}", provider=False
                )
                assert await core.register_plugin(consumer) is True
                consumers.append(consumer)

            # Property: every registered consumer has capability access
            for consumer in consumers:
                assert consumer.capability_granted, (
                    f"Consumer {consumer.metadata.name} should have capability access"
                )

            # Property: the provider recorded exactly one access per consumer
            assert provider.access_count.get("test_capability", 0) == num_consumers

        finally:
            if core.state == CoreState.RUNNING:
                await core.stop()


class OrchestrationTestPlugin(Plugin):
    """Test plugin for orchestration property testing."""

    def __init__(self, name=None, requires=None, provides=None):
        provides = provides or set()
        requires = requires or set()
        super().__init__(provides=provides, requires=requires, name=name)
        self.initialized = False
        self.access_count = {}

    async def on_start(self):
        self.initialized = True

    async def on_stop(self):
        self.initialized = False

    async def get_capability_stats(self):
        return dict(self.access_count)


class ProviderConsumerPlugin(Plugin):
    """Plugin that acts as either a capability provider or a consumer.

    Providers track how often each of their capabilities is accessed; consumers
    resolve their required capabilities in ``on_start`` and record one access
    on the real provider instance for each.
    """

    def __init__(self, capabilities, name=None, provider=False):
        if provider:
            super().__init__(provides=capabilities, name=name)
        else:
            super().__init__(requires=capabilities, name=name)

        self.capabilities = capabilities
        self.capability_granted = False
        self.access_count = {}
        self.provider = provider
        self.initialized = False

    async def on_start(self):
        self.initialized = True
        if self.provider:
            self.capability_granted = True
            return

        # Resolve each required capability and record the access on the
        # real provider instance (the collection returns proxies).
        for capability in self.capabilities:
            proxy = (await self.core.list()).capability.provides(capability).first()
            if proxy is None:
                raise RuntimeError(f"Capability {capability!r} has no provider")
            provider_obj = await proxy._get_object()
            if hasattr(provider_obj, "access_count"):
                provider_obj.access_count[capability] = (
                    provider_obj.access_count.get(capability, 0) + 1
                )
        self.capability_granted = True

    async def on_stop(self):
        self.initialized = False
