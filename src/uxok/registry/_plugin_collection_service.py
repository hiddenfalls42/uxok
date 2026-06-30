"""Manages PluginCollection caching and rebuilding."""

from __future__ import annotations

import asyncio
import weakref
from typing import TYPE_CHECKING, Any

from uxok.registry._plugin_view import CapabilityInfo, PluginCollection, PluginView

if TYPE_CHECKING:
    from uxok.protocols import PluginProtocol
    from uxok.protocols.registry import Registry


class PluginCollectionService:
    """Service responsible for building and caching PluginCollection.

    Caching strategy:
    - A dirty flag is set on register/unregister via invalidate().
    - list() returns the cached collection if clean, rebuilds if dirty.
    - Rebuild is O(N) with 2 lock acquisitions (all + dependency_graph).
    """

    def __init__(
        self,
        registry: Registry,
        capability_snapshot_fn: _CapabilitySnapshotFn | None = None,
    ) -> None:
        self._registry = registry
        self._capability_snapshot_fn = capability_snapshot_fn
        self._cached: PluginCollection | None = None
        self._dirty = True
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        """Mark cached collection as dirty."""
        self._dirty = True

    async def list(self) -> PluginCollection:
        """Get (and rebuild if needed) the plugin collection."""
        async with self._lock:
            if self._dirty or self._cached is None:
                await self._rebuild()
                self._dirty = False
            return self._cached  # type: ignore[return-value]

    async def _rebuild(self) -> None:
        """Rebuild the plugin collection from registry state.

        Single-pass O(N) algorithm:
        1. Fetch all plugins and the dependency graph (2 calls).
        2. Build a reverse-dep name map in one pass.
        3. Build PluginView objects in one pass.
        4. Snapshot capability info (O(C) over capabilities, not plugins).
        """
        all_plugins = await self._registry.all()
        dep_graph = await self._registry.dependency_graph()

        # Build reverse-dep map: plugin_name -> [names that depend on it]
        used_by: dict[str, list[str]] = {}
        for pid, deps in dep_graph.items():
            if pid not in all_plugins:
                continue
            depender_name = all_plugins[pid].metadata.name
            for dep_id in deps:
                if dep_id in all_plugins:
                    dep_name = all_plugins[dep_id].metadata.name
                    used_by.setdefault(dep_name, []).append(depender_name)

        # Build views in one pass
        views: list[PluginView] = []
        for order, plugin in enumerate(all_plugins.values(), 1):
            views.append(_build_view(plugin, order, used_by, self._registry))

        # Snapshot capability info if a provider function was supplied
        cap_info: dict[str, CapabilityInfo] | None = None
        if self._capability_snapshot_fn is not None:
            cap_info = self._capability_snapshot_fn()

        self._cached = PluginCollection(views, capability_info=cap_info)


# Type alias for the capability snapshot callable injected from the core.
_CapabilitySnapshotFn = Any  # Callable[[], dict[str, CapabilityInfo]]


def _build_view(
    plugin: PluginProtocol,
    load_order: int,
    used_by: dict[str, list[str]],
    registry: Registry,
) -> PluginView:
    """Build a PluginView for a single plugin."""
    meta = plugin.metadata
    hooks_provided = list(getattr(plugin, "_hooks", {}).keys())
    hooks_consumed = list(meta.hooks_consumed)
    events_subscribed = list(getattr(plugin, "_event_handlers", {}).keys())
    events_published = list(meta.events_published)

    # Pre-populate the weakref so status/ready resolve immediately without an
    # extra registry lookup.
    return PluginView(
        id=str(meta.id),
        name=meta.name,
        provides=set(meta.provides),
        requires=set(meta.requires),
        tags=set(meta.tags),
        used_by=used_by.get(meta.name, []),
        hooks_provided=hooks_provided,
        hooks_consumed=hooks_consumed,
        events_published=events_published,
        events_subscribed=events_subscribed,
        load_order=load_order,
        _registry=registry,
        _object_ref=weakref.ref(plugin),
    )
