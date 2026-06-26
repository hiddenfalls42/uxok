"""Capability system protocol definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from uxok.protocols._types import PluginId


@runtime_checkable
class CapabilitySystem(Protocol):
    """Capability system protocol for kernel-style plugin dependencies."""

    async def validate_requirements(self, plugin: Any) -> set[PluginId]:
        """Validate that plugin's required capabilities are available.

        Args:
            plugin: Plugin to validate requirements for

        Returns:
            Set of plugin IDs that provide required capabilities

        Raises:
            MissingCapabilityError: If required capabilities are not available
        """
        ...

    async def register_capabilities(self, plugin: Any) -> None:
        """Register capabilities provided by a plugin.

        Atomic: under ``error_on_conflict`` a collision is rejected before any
        capability is registered, so the operation is all-or-nothing.

        Args:
            plugin: Plugin providing capabilities

        Raises:
            PluginError: If capability collision policy is 'error_on_conflict'
                         or if the plugin fails protocol contract validation
        """
        ...

    async def get_capability(self, capability: str | type, *, tag: str | None = None) -> object:
        """Get plugin providing a capability with policy enforcement.

        Args:
            capability: Capability name (str) or Protocol type
            tag: Optional tag to filter providers by

        Returns:
            PluginProtocol providing capability, or None if missing policy allows

        Raises:
            KeyError: If capability not available and missing policy is 'raise',
                      or if tag is provided but no provider matches
        """
        ...

    async def unregister_capabilities_by_plugin(self, plugin_id: str) -> list[str]:
        """Unregister all capabilities provided by a specific plugin.

        Args:
            plugin_id: ID of the plugin to unregister capabilities for

        Returns:
            Names of capabilities whose last provider was this plugin (now fully
            revoked); the caller publishes ``core.capability.revoked`` for each.
        """
        ...

    async def swap_provider(
        self, old_provider: Any, new_provider: Any
    ) -> list[tuple[str, str, str]]:
        """Atomically reconcile capability providers during a hot-reload swap.

        Replaces the old instance in place for capabilities the new version
        still provides, inserts capabilities the new version adds, and removes
        capabilities the new version no longer provides — de-duplicating by
        plugin ID. Both providers must share the same plugin ID.

        Args:
            old_provider: Old plugin instance being replaced
            new_provider: New plugin instance to install

        Returns:
            ``(capability, old_provider_id, new_provider_id)`` tuples for
            capabilities whose provider instance was replaced in place; the
            caller publishes ``core.capability.rebound`` for each.

        Raises:
            ValueError: If providers have different IDs
            PluginError: If the new provider fails protocol contract validation
        """
        ...
