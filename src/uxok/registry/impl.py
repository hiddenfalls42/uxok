"""Simplified plugin registry implementation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from uxok.core._shared_utils import (
    format_plugin_error,
    log_op,
)

if TYPE_CHECKING:
    from collections.abc import Set

    from uxok.protocols import PluginProtocol
    from uxok.protocols._types import PluginId

logger = logging.getLogger(__name__)


class _Registry:
    """Simplified plugin registry with essential functionality.

    Features:
    - Simple dictionary-based storage
    - Dependency validation
    - Basic dependency resolution
    """

    def __init__(self, max_plugins: int | None = None) -> None:
        """Initialize registry.

        Args:
            max_plugins: Hard ceiling on registered plugins; None disables.
        """
        self._max_plugins = max_plugins

        # Plugin storage (plugin_id -> plugin)
        self._plugins: dict[PluginId, PluginProtocol] = {}

        # Dependency tracking (plugin_id -> set of dependency IDs)
        self._dependencies: dict[PluginId, set[PluginId]] = {}

        # Reverse dependency tracking (plugin_id -> set of dependent IDs)
        self._dependents: dict[PluginId, set[PluginId]] = {}

        # INVARIANT (lock-free by design): every read-modify-write of registry
        # state is a synchronous critical section — no await may appear inside
        # one. Under cooperative asyncio that makes each section atomic without
        # locks; the RWLock this replaced introduced the only suspension points
        # and was removed (decision record #12). If a future change must await
        # inside a mutation, reintroduce a lock around that section.

    async def add(
        self, plugin: PluginProtocol, additional_dependencies: set[PluginId] | None = None
    ) -> bool:
        """Add plugin to registry.

        Args:
            plugin: Plugin to add
            additional_dependencies: Additional runtime dependencies

        Returns:
            True if added successfully

        Raises:
            PluginError: If plugin validation fails
        """
        from uxok.errors import PluginError

        plugin_id = plugin.metadata.id
        plugin_name = plugin.metadata.name

        # Check if plugin already exists
        if plugin_id in self._plugins:
            # Allow updating dependencies for existing plugins (used in capability
            # resolution). Routed through the edge-replacement primitive so the
            # merge is validated, cycle-checked, and keeps reverse edges in sync.
            if additional_dependencies:
                merged = self._dependencies.get(plugin_id, set()) | set(additional_dependencies)
                self._replace_dependency_edges(plugin_id, merged)
            return True

        # Check for name conflicts
        for existing_plugin in self._plugins.values():
            if existing_plugin.metadata.name == plugin_name:
                raise PluginError(
                    format_plugin_error(
                        str(plugin_id),
                        f"name '{plugin_name}' already in use by plugin "
                        f"{existing_plugin.metadata.id}; plugin names must be unique — "
                        "pass name= to override the auto-derived class name",
                    )
                )

        # Enforce the plugin ceiling (insurance against runaway registration)
        if self._max_plugins is not None and len(self._plugins) >= self._max_plugins:
            raise PluginError(
                format_plugin_error(
                    str(plugin_id),
                    f"max_plugins limit reached ({self._max_plugins}); "
                    "unregister a plugin or raise CoreConfig.max_plugins",
                )
            )

        # Collect dependencies
        dependencies = set(plugin.metadata.dependencies)

        # Note: Capability-based dependencies are handled by CapabilitySystem
        # and passed via additional_dependencies parameter

        # Add additional dependencies
        if additional_dependencies:
            for dep_id in additional_dependencies:
                # Validate plugin ID exists
                if dep_id not in self._plugins:
                    raise PluginError(
                        format_plugin_error(
                            str(plugin_id), f"dependency plugin not found: {dep_id}"
                        )
                    )
                dependencies.add(dep_id)

        # Validate dependencies exist
        for dep_id in dependencies:
            if dep_id not in self._plugins:
                raise PluginError(
                    format_plugin_error(str(plugin_id), f"dependency not found: {dep_id}")
                )

        # Check for circular dependencies
        self._check_circular_dependencies(plugin_id, dependencies)

        # Add dependencies
        self._dependencies[plugin_id] = dependencies
        for dep_id in dependencies:
            if dep_id not in self._dependents:
                self._dependents[dep_id] = set()
            self._dependents[dep_id].add(plugin_id)

        # Add plugin
        self._plugins[plugin_id] = plugin
        logger.debug(
            "Added plugin to registry",
            extra=log_op("registry.add", plugin_id=str(plugin_id), plugin_name=plugin_name),
        )
        return True

    async def remove(self, plugin_id: PluginId, force: bool = False) -> bool:
        """Remove plugin from registry.

        Args:
            plugin_id: Plugin ID to remove
            force: If True, ignore dependency checks

        Returns:
            True if removed successfully

        Raises:
            PluginError: If other plugins depend on this one (unless force=True)
        """
        from uxok.errors import PluginError

        if plugin_id not in self._plugins:
            raise PluginError(format_plugin_error(str(plugin_id), "not found"))

        # Check for dependents
        if not force:
            dependents = self._dependents.get(plugin_id, set())
            active_dependents = {dep for dep in dependents if dep in self._plugins}
            if active_dependents:
                dependent_names = [self._plugins[dep].metadata.name for dep in active_dependents]
                raise PluginError(
                    format_plugin_error(
                        f"'{self._plugins[plugin_id].metadata.name}' ({plugin_id})",
                        f"dependents present -> {', '.join(dependent_names)}; "
                        "unregister the dependents first or pass force=True",
                    )
                )

        plugin_name = self._plugins[plugin_id].metadata.name

        # Remove dependencies
        for dep_id in self._dependencies.get(plugin_id, set()):
            if dep_id in self._dependents:
                self._dependents[dep_id].discard(plugin_id)

        # Remove dependents
        if plugin_id in self._dependents:
            del self._dependents[plugin_id]

        # Remove plugin
        del self._plugins[plugin_id]
        if plugin_id in self._dependencies:
            del self._dependencies[plugin_id]

        logger.debug(
            "Removed plugin from registry",
            extra=log_op("registry.remove", plugin_id=str(plugin_id), plugin_name=plugin_name),
        )
        return True

    async def swap_instance(
        self,
        plugin_id: PluginId,
        new_plugin: PluginProtocol,
        dependencies: set[PluginId] | None = None,
    ) -> None:
        """Atomically replace plugin instance while preserving ID.

        Used during hot reload to swap in a new plugin instance without
        unregistering the plugin. Validates that the new plugin has the
        same name as the old one to prevent accidental swapping of
        unrelated plugins.

        Args:
            plugin_id: ID of the plugin to replace
            new_plugin: New plugin instance to swap in
            dependencies: New dependency edges for the plugin. None preserves
                the existing edges; a set (possibly empty) replaces them,
                cycle-checked, with reverse edges reconciled.

        Raises:
            PluginError: If plugin_id not found, names don't match, a
                dependency is missing, or the new edges form a cycle
        """
        from uxok.errors import PluginError

        if plugin_id not in self._plugins:
            raise PluginError(format_plugin_error(str(plugin_id), "not found"))

        old_plugin = self._plugins[plugin_id]
        old_name = old_plugin.metadata.name
        new_name = new_plugin.metadata.name

        # Validate that names match - prevent swapping unrelated plugins
        if old_name != new_name:
            raise PluginError(
                format_plugin_error(
                    str(plugin_id),
                    f"cannot swap plugin with different name: '{old_name}' != '{new_name}'",
                )
            )

        if dependencies is not None:
            self._replace_dependency_edges(plugin_id, set(dependencies))

        self._plugins[plugin_id] = new_plugin
        logger.debug(
            "Swapped plugin instance in registry",
            extra=log_op(
                "registry.swap_instance",
                plugin_id=str(plugin_id),
                plugin_name=old_name,
            ),
        )

    def _replace_dependency_edges(self, plugin_id: PluginId, dependencies: set[PluginId]) -> None:
        """Replace a plugin's dependency edges (hot-reload / merge reconcile).

        Detaches the old edges first so the cycle check sees the graph as it
        will be, then installs the new edges; restores the old edges on
        failure. Synchronous critical section — no await (lock-free invariant).
        """
        from uxok.errors import PluginError

        for dep_id in dependencies:
            if dep_id not in self._plugins:
                raise PluginError(
                    format_plugin_error(str(plugin_id), f"dependency not found: {dep_id}")
                )

        old_deps = self._dependencies.get(plugin_id, set())
        for dep_id in old_deps:
            self._dependents.get(dep_id, set()).discard(plugin_id)
        self._dependencies.pop(plugin_id, None)

        try:
            self._check_circular_dependencies(plugin_id, dependencies)
        except PluginError:
            self._dependencies[plugin_id] = old_deps
            for dep_id in old_deps:
                self._dependents.setdefault(dep_id, set()).add(plugin_id)
            raise

        self._dependencies[plugin_id] = dependencies
        for dep_id in dependencies:
            self._dependents.setdefault(dep_id, set()).add(plugin_id)

    async def get(self, plugin_id: PluginId) -> PluginProtocol | None:
        """Get plugin by ID.

        Args:
            plugin_id: Plugin ID to look up

        Returns:
            Plugin if found, None otherwise
        """
        return self._plugins.get(plugin_id)

    async def all(self) -> dict[PluginId, PluginProtocol]:
        """Get all registered plugins.

        Returns:
            Dictionary of all plugins (ID -> plugin)
        """
        return self._plugins.copy()

    # ========== Internal methods for framework use ==========

    async def contains(self, plugin_id: PluginId) -> bool:
        """Check if plugin exists in registry.

        Args:
            plugin_id: Plugin ID to check

        Returns:
            True if plugin exists, False otherwise
        """
        return plugin_id in self._plugins

    async def dependencies(self, plugin_id: PluginId) -> set[PluginId]:
        """Get dependencies of a plugin.

        Args:
            plugin_id: ID of the plugin

        Returns:
            Set of dependency IDs
        """
        return self._dependencies.get(plugin_id, set()).copy()

    async def dependents(self, plugin_id: PluginId) -> set[PluginId]:
        """Get dependents of a plugin (plugins that rely on it).

        Args:
            plugin_id: ID of the plugin

        Returns:
            Set of dependent plugin IDs
        """
        return self._dependents.get(plugin_id, set()).copy()

    async def dependency_graph(self) -> dict[PluginId, set[PluginId]]:
        """Get all plugin dependencies.

        Returns:
            Dictionary of plugin ID to dependency IDs
        """
        return {pid: deps.copy() for pid, deps in self._dependencies.items()}

    async def load_order(self, plugin_ids: set[PluginId] | None = None) -> list[PluginId]:
        """Get plugins in dependency order for loading.

        Args:
            plugin_ids: Specific plugins to order (None for all)

        Returns:
            List of plugin IDs in dependency order
        """
        if plugin_ids is None:
            plugin_ids = set(self._plugins.keys())

        # Simple topological sort
        in_degree = dict.fromkeys(plugin_ids, 0)
        graph: dict[PluginId, set[PluginId]] = {pid: set() for pid in plugin_ids}

        # Build graph
        for pid in plugin_ids:
            deps = self._dependencies.get(pid, set()) & plugin_ids
            for dep in deps:
                graph[dep].add(pid)
                in_degree[pid] += 1

        # Topological sort
        result = []
        queue = [pid for pid, degree in in_degree.items() if degree == 0]

        while queue:
            current = queue.pop(0)
            result.append(current)

            for dependent in graph[current]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        # Check for cycles
        if len(result) != len(plugin_ids):
            from uxok.errors import CoreError

            raise CoreError("Circular dependency detected in plugin load order")

        return result

    def _check_circular_dependencies(
        self, plugin_id: PluginId, dependencies: Set[PluginId]
    ) -> None:
        """Reject dependency edges that would create a cycle.

        Installing edges plugin_id → dependencies creates a cycle exactly when
        plugin_id is reachable from any of those dependencies through the
        existing graph (callers install plugin_id's outgoing edges after this
        check — or, on the swap path, have already detached the old ones — so
        the traversal sees the graph as it will be). Iterative DFS; the
        visited set also terminates traversal of any pre-existing cycle.

        Args:
            plugin_id: Plugin whose edges are being installed
            dependencies: Proposed dependencies of that plugin

        Raises:
            PluginError: If the proposed edges would close a cycle
        """
        from uxok.errors import PluginError

        visited: set[PluginId] = set()
        stack: list[PluginId] = list(dependencies)
        while stack:
            node = stack.pop()
            if node == plugin_id:
                raise PluginError(
                    f"Circular dependency detected: plugin {plugin_id} is reachable "
                    f"from its own proposed dependencies"
                )
            if node in visited:
                continue
            visited.add(node)
            stack.extend(self._dependencies.get(node, set()))
