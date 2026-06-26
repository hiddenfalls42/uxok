"""PluginProtocol registry protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from uxok.protocols._types import PluginId
from uxok.protocols.plugin import PluginProtocol


@runtime_checkable
class Registry(Protocol):
    """Zero-contention plugin registry protocol."""

    async def add(
        self, plugin: PluginProtocol, additional_dependencies: set[PluginId] | None = None
    ) -> bool:
        """Add a plugin to the registry.

        Args:
            plugin: The plugin to add
            additional_dependencies: Additional dependencies to track (from capability requirements)

        Returns:
            True if added successfully

        Raises:
            PluginError: If plugin already exists
        """
        ...

    async def remove(self, plugin_id: PluginId, force: bool = False) -> bool:
        """Remove a plugin from the registry.

        Args:
            plugin_id: ID of the plugin to remove
            force: If True, allow removal even if plugin has dependents (for hot-reload)

        Returns:
            True if removed successfully

        Raises:
            PluginError: If plugin doesn't exist or has dependents (unless force=True)
        """
        ...

    async def get(self, plugin_id: PluginId) -> PluginProtocol | None:
        """Get a plugin from the registry.

        Args:
            plugin_id: ID of the plugin to get

        Returns:
            The plugin if found, None otherwise
        """
        ...

    async def all(self) -> dict[PluginId, PluginProtocol]:
        """Get all registered plugins.

        Returns:
            Dictionary of plugin ID to plugin
        """
        ...

    async def contains(self, plugin_id: PluginId) -> bool:
        """Check if a plugin is in the registry.

        Args:
            plugin_id: ID of the plugin to check

        Returns:
            True if plugin exists in registry
        """
        ...

    async def dependents(self, plugin_id: PluginId) -> set[PluginId]:
        """Get plugins that depend on a given plugin.

        Args:
            plugin_id: ID of the plugin

        Returns:
            Set of plugin IDs that depend on this plugin
        """
        ...

    async def dependencies(self, plugin_id: PluginId) -> set[PluginId]:
        """Get dependencies of a plugin.

        Args:
            plugin_id: ID of the plugin

        Returns:
            Set of dependency IDs
        """
        ...

    async def dependency_graph(self) -> dict[PluginId, set[PluginId]]:
        """Get all plugin dependencies.

        Returns:
            Dictionary of plugin ID to dependency IDs
        """
        ...

    async def load_order(self, plugin_ids: set[PluginId] | None = None) -> list[PluginId]:
        """Get plugins in dependency order for loading.

        Args:
            plugin_ids: Specific plugins to order (None for all)

        Returns:
            List of plugin IDs in dependency order
        """
        ...

    async def block(self, identifier: str) -> None:
        """Block plugin from registration.

        Thread-safe operation that acquires write lock.

        Args:
            identifier: PluginProtocol name or ID to block
        """
        ...

    async def unblock(self, identifier: str) -> bool:
        """Unblock plugin.

        Thread-safe operation that acquires write lock.

        Args:
            identifier: PluginProtocol name or ID to unblock

        Returns:
            True if plugin was blocked
        """
        ...

    def is_blocked(self, identifier: str) -> bool:
        """Check if plugin is blocked.

        Args:
            identifier: PluginProtocol name or ID to check

        Returns:
            True if plugin is blocked
        """
        ...

    async def swap_instance(
        self,
        plugin_id: PluginId,
        new_plugin: PluginProtocol,
        dependencies: set[PluginId] | None = None,
    ) -> None:
        """Atomically replace plugin instance while preserving ID.

        This is used by the hot-reload path to enable zero-downtime swaps.

        Args:
            plugin_id: ID of the plugin to replace
            new_plugin: New plugin instance (must have same name)
            dependencies: New dependency edges. None preserves the existing
                edges; a set (possibly empty) replaces them, cycle-checked,
                with reverse edges reconciled.

        Raises:
            PluginError: If plugin_id not found, names don't match, a
                dependency is missing, or the new edges would form a cycle
        """
        ...
