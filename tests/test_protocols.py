"""
Baseline tests for protocol interfaces.

Tests that protocol definitions work correctly and maintain their contracts.
These tests should pass before and after refactoring.
"""

from dataclasses import is_dataclass
from typing import runtime_checkable

from uxok.protocols import (
    Core,
    CoreConfig,
    CoreState,
    Event,
    Hook,
    PluginMetadata,
    PluginProtocol,
)
from uxok.protocols.events import EventBus
from uxok.protocols.hooks import HookSystem
from uxok.protocols.registry import Registry


class TestProtocolDefinitions:
    """Test that all protocol definitions are properly defined."""

    def test_core_protocol_is_protocol(self):
        """Core should be a Protocol."""
        assert isinstance(Core, type)
        assert hasattr(Core, "__protocol_attrs__") or runtime_checkable(Core)

    def test_plugin_protocol_is_protocol(self):
        """PluginProtocol should be a Protocol."""
        assert isinstance(PluginProtocol, type)
        assert hasattr(PluginProtocol, "__protocol_attrs__") or runtime_checkable(PluginProtocol)

    def test_eventbus_protocol_is_protocol(self):
        """EventBus should be a Protocol."""
        assert isinstance(EventBus, type)
        assert hasattr(EventBus, "__protocol_attrs__") or runtime_checkable(EventBus)

    def test_hooksystem_protocol_is_protocol(self):
        """HookSystem should be a Protocol."""
        assert isinstance(HookSystem, type)
        assert hasattr(HookSystem, "__protocol_attrs__") or runtime_checkable(HookSystem)

    def test_registry_protocol_is_protocol(self):
        """Registry should be a Protocol."""
        assert isinstance(Registry, type)
        assert hasattr(Registry, "__protocol_attrs__") or runtime_checkable(Registry)


class TestDataClasses:
    """Test that dataclasses are properly defined."""

    def test_core_config_is_dataclass(self):
        """CoreConfig should be a dataclass."""
        assert is_dataclass(CoreConfig)

    def test_plugin_metadata_is_dataclass(self):
        """PluginMetadata should be a dataclass."""
        assert is_dataclass(PluginMetadata)

    def test_event_is_dataclass(self):
        """Event should be a dataclass."""
        assert is_dataclass(Event)

    def test_hook_is_dataclass(self):
        """Hook should be a dataclass."""
        assert is_dataclass(Hook)

    def test_core_config_has_defaults(self):
        """CoreConfig should instantiate with defaults."""
        config = CoreConfig()
        assert config.max_plugins > 0


class TestCoreState:
    """Test CoreState enum."""

    def test_core_state_enum_values(self):
        """CoreState should have expected values."""
        print(f"CoreState module: {CoreState.__module__}")
        print(f"CoreState values: {list(CoreState)}")
        print(f"CoreState dir: {[attr for attr in dir(CoreState) if not attr.startswith('_')]}")
        assert hasattr(CoreState, "INITIALIZED")
        assert hasattr(CoreState, "RUNNING")
        assert hasattr(CoreState, "STOPPED")
        assert hasattr(CoreState, "FAILED")

    def test_core_state_values_are_unique(self):
        """CoreState values should be unique."""
        states = [
            CoreState.INITIALIZED,
            CoreState.RUNNING,
            CoreState.STOPPED,
            CoreState.FAILED,
        ]
        assert len(states) == len(set(states))


class TestProtocolImports:
    """Test that all protocol imports work."""

    def test_can_import_protocols_from_protocols_package(self):
        """Curated protocols import from the package; EventBus/HookSystem live in
        their definition modules (not part of the public ``protocols.__all__``)."""
        from uxok.protocols import CoreConfig, PluginProtocol
        from uxok.protocols.events import EventBus
        from uxok.protocols.hooks import HookSystem
        from uxok.protocols.registry import Registry

        assert CoreConfig is not None
        assert EventBus is not None
        assert HookSystem is not None
        assert PluginProtocol is not None
        assert Registry is not None

    def test_can_import_public_api_from_top_level(self):
        """Public API symbols should be importable from uxok top-level."""
        from uxok import Core, Plugin, event, hook

        assert Core is not None
        assert Plugin is not None
        assert hook is not None
        assert event is not None


class TestPluginMetadata:
    """Test PluginMetadata dataclass."""

    def test_plugin_metadata_creation(self):
        """PluginMetadata should be creatable with required fields."""
        from uuid import uuid4

        metadata = PluginMetadata(
            id=uuid4(),
            name="test_plugin",
            version="1.0.0",
            description="Test plugin",
        )
        assert metadata.name == "test_plugin"
        assert metadata.version == "1.0.0"
        assert metadata.description == "Test plugin"


class TestEvent:
    """Test Event dataclass."""

    def test_event_creation(self):
        """Event should be creatable with name and data."""
        event = Event(name="test.event", data={"key": "value"})
        assert event.name == "test.event"
        assert event.data == {"key": "value"}


class TestHook:
    """Test Hook dataclass."""

    def test_hook_creation(self):
        """Hook should be creatable with required fields."""

        async def test_func():
            pass

        hook = Hook(
            name="test.hook",
            callback=test_func,
            priority=10,
        )
        assert hook.name == "test.hook"
        assert hook.func == test_func
        assert hook.priority == 10


# =============================================================================
# Error class constructors
# =============================================================================

from uxok.errors import (
    CapabilityError,
    MissingCapabilityError,
)


class TestCapabilityErrorConstructors:
    """Test all CapabilityError constructor branches."""

    def test_single_capability(self):
        err = CapabilityError("storage")
        assert "storage" in str(err)
        assert "not available" in str(err)

    def test_single_with_available(self):
        err = CapabilityError("storage", available=["database", "cache"])
        assert "storage" in str(err)
        assert "database" in str(err)
        assert "cache" in str(err)
        assert "Did you forget" in str(err)

    def test_list_capabilities(self):
        err = CapabilityError(["storage", "compute"])
        assert "compute" in str(err)
        assert "storage" in str(err)

    def test_list_with_available(self):
        err = CapabilityError(["storage"], available=["cache"])
        assert "storage" in str(err)
        assert "cache" in str(err)

    def test_none_capability(self):
        err = CapabilityError(None)
        assert "Capability error" in str(err)

    def test_with_message(self):
        err = CapabilityError("x", message="custom msg")
        assert str(err) == "custom msg"


class TestMissingCapabilityError:
    def test_basic(self):
        err = MissingCapabilityError(["storage"], phase="register")
        assert "storage" in str(err)
        assert "register" in str(err)
        assert err.missing == ["storage"]
        assert err.phase == "register"

    def test_with_available(self):
        err = MissingCapabilityError(["x"], phase="start", available=["y", "z"])
        assert "y" in str(err)
        assert "z" in str(err)
