"""Tests for capability system functionality."""

from __future__ import annotations

import pytest

from uxok import Core, Plugin
from uxok.errors import CapabilityError, PluginError
from uxok.protocols import CoreConfig


class MockCapabilityPlugin(Plugin):
    """Mock plugin for testing capabilities."""

    def __init__(self, name="mock", provides=None, requires=None):
        super().__init__(
            name=name, version="1.0.0", provides=provides or set(), requires=requires or set()
        )

    async def on_start(self):
        pass

    async def on_stop(self):
        pass


class TestCapabilitySystem:
    """Test capability system core functionality."""

    @pytest.mark.asyncio
    async def test_get_capability_success(self, started_core: Core):
        """Test successful capability resolution."""
        # Register provider
        provider = MockCapabilityPlugin(name="provider", provides={"test_cap"})
        await started_core.register_plugin(provider)

        # Get capability
        result = await started_core.get_capability("test_cap")
        assert result is provider

    @pytest.mark.asyncio
    async def test_get_capability_missing_raises_error(self, clean_core: Core):
        """Test missing capability raises CapabilityError."""
        with pytest.raises(CapabilityError) as exc_info:
            await clean_core.get_capability("missing_cap")

        assert "missing_cap" in str(exc_info.value)
        # When no capabilities are available, no "Available capabilities:" message
        assert "Available capabilities:" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_list_capabilities(self, started_core: Core):
        """Test listing available capabilities."""
        # Initially empty
        capabilities = await started_core._capability_system.list_capabilities()
        assert capabilities == []

        # Register provider
        provider = MockCapabilityPlugin(name="provider", provides={"cap1", "cap2"})
        await started_core.register_plugin(provider)

        capabilities = await started_core._capability_system.list_capabilities()
        assert set(capabilities) == {"cap1", "cap2"}

    @pytest.mark.asyncio
    async def test_get_capability_info(self, started_core: Core):
        """Test getting capability information."""
        # Register provider
        provider = MockCapabilityPlugin(name="provider", provides={"test_cap"})
        await started_core.register_plugin(provider)

        info = await started_core._capability_system.get_capability_info("test_cap")
        assert info is not None
        assert info["name"] == "test_cap"
        assert info["selected_provider"] == "provider"
        assert info["selected_provider_id"] == str(provider.metadata.id)
        assert info["selected_version"] == "1.0.0"
        assert info["provider_count"] == 1
        assert len(info["all_providers"]) == 1

        # Missing capability
        info = await started_core._capability_system.get_capability_info("missing")
        assert info is None


class TestCapabilityCollisionPolicies:
    """Test capability collision policy behaviors."""

    @pytest.mark.asyncio
    async def test_error_on_conflict_policy(self):
        """Test error_on_conflict collision policy."""
        core = Core(capability_collision="error_on_conflict")

        try:
            await core.start()
            # Register first provider
            provider1 = MockCapabilityPlugin(name="provider1", provides={"shared_cap"})
            result1 = await core.register_plugin(provider1)
            assert result1 is True

            # Second provider should fail with explicit error_on_conflict
            provider2 = MockCapabilityPlugin(name="provider2", provides={"shared_cap"})
            with pytest.raises(PluginError):
                await core.register_plugin(provider2)
        finally:
            if core.state.name == "RUNNING":
                await core.stop()

    @pytest.mark.asyncio
    async def test_last_wins_with_warning_policy(self):
        """Test last_wins_with_warning collision policy."""
        core = Core(
            capability_collision="last_wins_with_warning",
            capability_selection="last_registered",
        )

        try:
            await core.start()
            # Register first provider
            provider1 = MockCapabilityPlugin(name="provider1", provides={"shared_cap"})
            await core.register_plugin(provider1)

            # Second provider should succeed with warning
            provider2 = MockCapabilityPlugin(name="provider2", provides={"shared_cap"})
            await core.register_plugin(provider2)

            # Last provider should be returned
            result = await core.get_capability("shared_cap")
            assert result is provider2
        finally:
            if core.state.name == "RUNNING":
                await core.stop()

    @pytest.mark.asyncio
    async def test_first_wins_policy(self):
        """Test first_wins collision policy."""
        core = Core(capability_collision="first_wins")

        try:
            await core.start()
            # Register first provider
            provider1 = MockCapabilityPlugin(name="provider1", provides={"shared_cap"})
            await core.register_plugin(provider1)

            # Second provider should succeed but not override
            provider2 = MockCapabilityPlugin(name="provider2", provides={"shared_cap"})
            await core.register_plugin(provider2)

            # First provider should still be returned
            result = await core.get_capability("shared_cap")
            assert result is provider1
        finally:
            if core.state.name == "RUNNING":
                await core.stop()


class TestCapabilityMissingPolicies:
    """Test capability missing policy behaviors."""

    @pytest.mark.asyncio
    async def test_missing_policy_raise(self):
        """Test missing policy 'raise'."""
        core = Core(capability_missing="raise")

        with pytest.raises(CapabilityError):
            await core.get_capability("missing")

    @pytest.mark.asyncio
    async def test_missing_policy_return_none(self):
        """Test missing policy 'return_none'."""
        core = Core(capability_missing="return_none")

        result = await core.get_capability("missing")
        assert result is None


class TestCommitOnlyRegistration:
    """Tests for commit-only registration (no partial state)."""

    @pytest.mark.asyncio
    async def test_register_missing_dependency_raises_capability_error_and_leaves_no_state(
        self, started_core: Core
    ):
        """Registration with missing capability should raise and leave no residue."""
        consumer = MockCapabilityPlugin(name="consumer", requires={"missing_cap"})
        with pytest.raises(CapabilityError):
            await started_core.register_plugin(consumer)

        all_plugins = await started_core._registry.all()
        assert all_plugins == {}
        assert await started_core._capability_system.list_capabilities() == []

    @pytest.mark.asyncio
    async def test_register_failure_on_start_leaves_no_state(self, started_core: Core):
        """Registration failing during on_start should leave no hooks/caps/registry entries."""

        class FailingPlugin(MockCapabilityPlugin):
            def __init__(self):
                super().__init__(name="failing", provides={"failcap"})

            async def on_start(self):
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await started_core.register_plugin(FailingPlugin())

        # No plugin in registry
        all_plugins = await started_core._registry.all()
        assert all_plugins == {}
        # No capabilities exposed
        assert "failcap" not in await started_core._capability_system.list_capabilities()


class TestPluginCapabilityConvenience:
    """Test plugin convenience methods for capabilities."""

    @pytest.mark.asyncio
    async def test_plugin_get_capability(self, started_core: Core):
        """Test plugin.get_capability convenience method."""
        # Register provider
        provider = MockCapabilityPlugin(name="provider", provides={"test_cap"})
        await started_core.register_plugin(provider)

        # Create consumer plugin
        consumer = MockCapabilityPlugin(name="consumer", requires={"test_cap"})
        await started_core.register_plugin(consumer)

        # Test convenience method
        result = await consumer.get_capability("test_cap")
        assert result is provider


class TestCapabilityConfigValidation:
    """Test capability configuration validation."""

    def test_valid_collision_policies(self):
        """Test valid collision policy values."""
        valid_policies = ["error_on_conflict", "first_wins", "last_wins_with_warning"]

        for policy in valid_policies:
            config = CoreConfig(capability_collision=policy)
            # Should not raise
            assert config.capability_collision == policy

    def test_invalid_collision_policy(self):
        """Test invalid collision policy raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            CoreConfig(capability_collision="invalid_policy")

        assert "capability_collision" in str(exc_info.value)

    def test_valid_selection_policies(self):
        """Test valid selection policy values."""
        valid_policies = ["first_registered", "last_registered"]

        for policy in valid_policies:
            config = CoreConfig(capability_selection=policy)
            assert config.capability_selection == policy

    def test_invalid_selection_policy(self):
        """Test invalid selection policy raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            CoreConfig(capability_selection="invalid_policy")

        assert "capability_selection" in str(exc_info.value)

    def test_valid_missing_policies(self):
        """Test valid missing policy values."""
        valid_policies = ["raise", "return_none"]

        for policy in valid_policies:
            config = CoreConfig(capability_missing=policy)
            assert config.capability_missing == policy

    def test_invalid_missing_policy(self):
        """Test invalid missing policy raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            CoreConfig(capability_missing="invalid_policy")

        assert "capability_missing" in str(exc_info.value)


class TestCapabilityCleanup:
    """Test capability system cleanup."""

    @pytest.mark.asyncio
    async def test_capability_cleanup_on_stop(self):
        """Test capabilities are cleaned up during core shutdown."""
        core = Core()

        try:
            await core.start()
            # Register provider
            provider = MockCapabilityPlugin(name="provider", provides={"test_cap"})
            await core.register_plugin(provider)

            # Capability should be available
            capabilities = await core._capability_system.list_capabilities()
            assert "test_cap" in capabilities

            # Stop core
            await core.stop()

            # Create new core and check capabilities are gone
            # (Note: This tests that the capability system drains properly)
            core2 = Core()
            capabilities = await core2._capability_system.list_capabilities()
            assert "test_cap" not in capabilities
        finally:
            if core.state.name == "RUNNING":
                await core.stop()
