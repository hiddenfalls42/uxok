"""Plugin view system: descriptive snapshots with benign live reads.

A ``PluginView`` describes a plugin and exposes only benign live observations
(``status``/``ready``/``uptime``/``methods``); it is **not** a handle and offers
no way to invoke a method on, or hand back, the live instance (RFC 0001 §3.2.2 —
discovery must not be a backdoor to invocation).
"""

from __future__ import annotations

import time
import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from uxok.errors import StalePluginError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from uxok.protocols import PluginProtocol
    from uxok.protocols.registry import Registry


@dataclass(frozen=True)
class CapabilityInfo:
    """Typed result for capability-protocol introspection.

    Mirrors the shape of the old ``get_capability_info`` dict but as a frozen
    dataclass with typed fields.  ``protocol_name`` and ``protocol_methods``
    are populated only when the capability was registered with a Protocol type;
    they are empty strings / empty lists otherwise (``typed == False``).
    """

    name: str
    """Capability name."""

    providers: list[dict[str, Any]]
    """Provider descriptors: name, id, version, description, tags."""

    selected_provider: str
    """Name of the currently selected provider (per the capability_selection policy)."""

    provider_count: int
    """Total number of registered providers."""

    typed: bool
    """True when a Protocol type was associated with this capability at registration."""

    protocol_name: str
    """Protocol class name; empty string when ``typed`` is False."""

    protocol_methods: list[dict[str, Any]]
    """``get_protocol_methods`` output; empty list when ``typed`` is False."""


@dataclass
class PluginView:
    """Descriptive snapshot of a plugin — a description, not a handle.

    It exposes no way to invoke a method on, or hand back, the live instance
    (RFC 0001 §3.2.2): discovery must not be a backdoor to invocation. To act on
    a plugin, resolve it through the ``kernel.lifecycle`` grant (``get_plugin``)
    or a typed capability.

    **Descriptive fields** (frozen at collection-build time; fresh on every
    ``core.list()`` call because the collection is rebuilt when the registry
    changes):

        id, name, provides, requires, tags, used_by, hooks_provided,
        hooks_consumed, events_published, events_subscribed, load_order

    These are used for listing, filtering, and indexing.

    **Benign live reads** (resolve the current live instance on access but return
    only data — never the instance, never arbitrary invocation):

    - ``status`` (sync property) — derives ``"created" | "active" | "stopped"``
      from the live instance flags via the weakref fast-path.  Returns
      ``"stopped"`` when the plugin is no longer resolvable (it was torn down
      after this view was fetched).
    - ``ready`` (sync property) — ``True`` when ``status == "active"``.
    - ``uptime`` (async) — seconds since plugin start from the live instance's
      ``_start_time``; raises ``StalePluginError`` when the plugin is gone
      (uptime is a liveness claim, not a description).
    - ``methods`` (async) — the concrete plugin class's own public methods,
      excluding anything inherited from the ``Plugin`` base class; raises
      ``StalePluginError`` when the plugin is gone.
    """

    # Descriptive snapshot fields — fresh-at-fetch, used for filtering/indexing
    id: str
    name: str
    provides: set[str]
    requires: set[str]
    tags: set[str]
    used_by: list[str]
    hooks_provided: list[str]
    hooks_consumed: list[str]
    events_published: list[str]
    events_subscribed: list[str]
    load_order: int

    # Object resolution (private)
    _registry: Registry = field(repr=False)
    _object_ref: weakref.ReferenceType | None = field(default=None, repr=False)

    @property
    def status(self) -> Literal["created", "active", "stopped"]:
        """Live plugin lifecycle status, derived from the current instance flags.

        Uses the weakref fast-path: if the cached weakref is live, derives the
        status from ``_shutdown`` / ``_initialized`` flags directly.  If the
        weakref is dead (or was never set) the plugin is no longer resolvable
        and ``"stopped"`` is returned.  This is a sync property — no await —
        which keeps ``PluginCollection.active`` synchronous.
        """
        if self._object_ref is not None:
            obj = self._object_ref()
            if obj is not None:
                return _plugin_status_from_instance(obj)
        # Weakref is dead or was never populated — plugin is gone.
        return "stopped"

    @property
    def ready(self) -> bool:
        """True if the plugin is currently active and ready for method calls."""
        return self.status == "active"

    def invalidate_cache(self) -> None:
        """Invalidate cached object references."""
        self._object_ref = None

    async def uptime(self) -> float:
        """Seconds since plugin start (live, always current).

        Raises:
            StalePluginError: If the plugin is no longer resolvable.
        """
        obj = await self._get_object()
        if obj is None:
            raise StalePluginError(
                f"Plugin '{self.name}' (id={self.id}) is no longer resolvable; "
                "it was unregistered or torn down after this view was fetched."
            )
        start_time = getattr(obj, "_start_time", None)
        if start_time is None:
            return 0.0
        return time.time() - start_time

    async def methods(self) -> list[dict[str, Any]]:
        """Public methods defined on the concrete plugin class.

        Excludes methods inherited from the ``Plugin`` base class (``emit``,
        ``config``, ``hook``, ``create_background_task``, ``on_start``,
        ``on_stop``, etc.).  Returns the same dict shape as
        ``get_protocol_methods``: ``name, signature, parameters,
        return_annotation, doc``.

        Raises:
            StalePluginError: If the plugin is no longer resolvable.
        """
        from uxok.utils import get_instance_methods

        obj = await self._get_object()
        if obj is None:
            raise StalePluginError(
                f"Plugin '{self.name}' (id={self.id}) is no longer resolvable; "
                "it was unregistered or torn down after this view was fetched."
            )
        return get_instance_methods(obj)

    async def _get_object(self) -> PluginProtocol | None:
        """Resolve the live plugin object, registry-authoritative.

        Actions are EAFP: the registry (membership), not object liveness, is the
        source of truth. We deliberately do NOT short-circuit on the cached
        weakref here — a plugin can be unregistered while a strong reference to
        the instance still exists, and an action on such a view must raise rather
        than silently invoke a torn-down plugin. The weakref is refreshed below
        only as a cache for the sync ``status`` read.
        """
        # Prefer ID lookup (exact match, no conflicts)
        try:
            plugin_id = UUID(self.id)
            obj = await self._registry.get(plugin_id)
            if obj is not None:
                self._object_ref = weakref.ref(obj)
                return obj
        except (ValueError, TypeError):
            pass

        # Fallback: name scan
        all_plugins = await self._registry.all()
        matching = [p for p in all_plugins.values() if p.metadata.name == self.name]

        if len(matching) > 1:
            ids = [str(p.metadata.id) for p in matching]
            raise ValueError(
                f"Plugin name '{self.name}' is ambiguous - "
                f"multiple plugins found: {ids}. "
                "Use plugin ID instead of name for resolution."
            )
        if len(matching) == 1:
            self._object_ref = weakref.ref(matching[0])
            return matching[0]

        return None

    # NOTE: no __getattr__ delegation, deliberately. PluginView is a descriptive
    # snapshot, not a handle — it exposes no way to invoke a method on, or hand
    # back, the live instance (RFC 0001 §3.2.2: discovery must not be a backdoor
    # to invocation). To act on a plugin, resolve it through the kernel.lifecycle
    # grant (get_plugin) or a typed capability. The private _get_object below
    # backs only the benign data reads uptime()/methods().


def _plugin_status_from_instance(obj: Any) -> Literal["created", "active", "stopped"]:
    """Derive lifecycle status from a live plugin instance's flags."""
    if getattr(obj, "_shutdown", False):
        return "stopped"
    if getattr(obj, "_initialized", False):
        return "active"
    return "created"


class _FilterProxy:
    """Proxy for composable filtering.

    Uses the parent collection's indexes for O(1) lookups when
    called from a root collection, and falls back to linear scan
    on sub-collections.
    """

    def __init__(
        self,
        plugins: list[PluginView],
        filter_type: str,
        indexes: _CollectionIndexes | None = None,
        capability_info: dict[str, CapabilityInfo] | None = None,
    ) -> None:
        self._plugins = plugins
        self._type = filter_type
        self._indexes = indexes
        self._capability_info = capability_info

    def provides(self, name: str) -> PluginCollection:
        """Filter by what this type provides."""
        # Fast path: use indexes if available
        if self._indexes is not None:
            if self._type == "capability":
                return PluginCollection(self._indexes.by_capability.get(name, []))
            if self._type == "hook":
                return PluginCollection(self._indexes.by_hook.get(name, []))
            if self._type == "event":
                return PluginCollection(self._indexes.by_event_pub.get(name, []))

        # Slow path: linear scan on sub-collections
        if self._type == "capability":
            filtered = [p for p in self._plugins if name in p.provides]
        elif self._type == "hook":
            filtered = [p for p in self._plugins if name in p.hooks_provided]
        elif self._type == "event":
            filtered = [p for p in self._plugins if name in p.events_published]
        else:
            filtered = []
        return PluginCollection(filtered)

    def consumes(self, name: str) -> PluginCollection:
        """Filter by what this type consumes."""
        # Fast path: use indexes if available
        if self._indexes is not None:
            if self._type == "capability":
                return PluginCollection(self._indexes.by_requires.get(name, []))
            if self._type == "hook":
                return PluginCollection(self._indexes.by_hook_consumed.get(name, []))
            if self._type == "event":
                return PluginCollection(self._indexes.by_event_sub.get(name, []))

        # Slow path: linear scan on sub-collections
        if self._type == "capability":
            filtered = [p for p in self._plugins if name in p.requires]
        elif self._type == "hook":
            filtered = [p for p in self._plugins if name in p.hooks_consumed]
        elif self._type == "event":
            filtered = [p for p in self._plugins if name in p.events_subscribed]
        else:
            filtered = []
        return PluginCollection(filtered)

    def info(self, name: str) -> CapabilityInfo | None:
        """Return typed capability-protocol info for a capability (capability filter only).

        Returns ``None`` for non-capability filter types, or when ``name`` is
        not a known capability.

        Args:
            name: Capability name to look up.

        Returns:
            ``CapabilityInfo`` when available; ``None`` otherwise.
        """
        if self._type != "capability":
            return None
        if self._capability_info is None:
            return None
        return self._capability_info.get(name)


class _CollectionIndexes:
    """Pre-built indexes for O(1) collection lookups.

    Built once at PluginCollection construction time.
    """

    __slots__ = (
        "by_capability",
        "by_event_pub",
        "by_event_sub",
        "by_hook",
        "by_hook_consumed",
        "by_id",
        "by_name",
        "by_requires",
    )

    def __init__(self, plugins: list[PluginView]) -> None:
        self.by_name: dict[str, PluginView] = {}
        self.by_id: dict[str, PluginView] = {}
        self.by_capability: dict[str, list[PluginView]] = {}
        self.by_requires: dict[str, list[PluginView]] = {}
        self.by_hook: dict[str, list[PluginView]] = {}
        self.by_hook_consumed: dict[str, list[PluginView]] = {}
        self.by_event_pub: dict[str, list[PluginView]] = {}
        self.by_event_sub: dict[str, list[PluginView]] = {}

        for p in plugins:
            self.by_name[p.name] = p
            self.by_id[p.id] = p
            for cap in p.provides:
                self.by_capability.setdefault(cap, []).append(p)
            for req in p.requires:
                self.by_requires.setdefault(req, []).append(p)
            for h in p.hooks_provided:
                self.by_hook.setdefault(h, []).append(p)
            for h in p.hooks_consumed:
                self.by_hook_consumed.setdefault(h, []).append(p)
            for e in p.events_published:
                self.by_event_pub.setdefault(e, []).append(p)
            for e in p.events_subscribed:
                self.by_event_sub.setdefault(e, []).append(p)


class PluginCollection:
    """Collection of PluginView objects with indexed lookups.

    Root collections (from core.list()) have pre-built indexes
    for O(1) name/id/capability lookups. Sub-collections from
    filtering do not build indexes (they are usually small and
    short-lived).
    """

    def __init__(
        self,
        plugins: list[PluginView],
        *,
        build_indexes: bool = True,
        capability_info: dict[str, CapabilityInfo] | None = None,
    ) -> None:
        self._plugins = plugins
        self._indexes: _CollectionIndexes | None = None
        self._capability_info = capability_info
        if build_indexes and plugins:
            self._indexes = _CollectionIndexes(plugins)

    @property
    def active(self) -> PluginCollection:
        return PluginCollection(
            [p for p in self._plugins if p.status == "active"],
            build_indexes=False,
        )

    @property
    def capability(self) -> _FilterProxy:
        return _FilterProxy(self._plugins, "capability", self._indexes, self._capability_info)

    @property
    def hook(self) -> _FilterProxy:
        return _FilterProxy(self._plugins, "hook", self._indexes)

    @property
    def event(self) -> _FilterProxy:
        return _FilterProxy(self._plugins, "event", self._indexes)

    async def uptime_over(self, seconds: float) -> PluginCollection:
        """Return plugins whose uptime exceeds ``seconds``.

        This is now async because ``PluginView.uptime()`` resolves the live
        instance.  Plugins that are no longer resolvable (``StalePluginError``)
        are excluded from the result rather than propagating the error, since
        a collection filter should not fail due to a single stale entry.
        """
        result: list[PluginView] = []
        for p in self._plugins:
            try:
                ut = await p.uptime()
                if ut > seconds:
                    result.append(p)
            except StalePluginError:
                pass
        return PluginCollection(result, build_indexes=False)

    def by_name(self, name: str) -> PluginView | None:
        if self._indexes is not None:
            return self._indexes.by_name.get(name)
        return next((p for p in self._plugins if p.name == name), None)

    def by_id(self, plugin_id: str | UUID) -> PluginView | None:
        pid_str = str(plugin_id)
        if self._indexes is not None:
            return self._indexes.by_id.get(pid_str)
        return next((p for p in self._plugins if p.id == pid_str), None)

    def first(self) -> PluginView | None:
        """Get the first plugin, or None if empty."""
        return self._plugins[0] if self._plugins else None

    def __iter__(self) -> Iterator[PluginView]:
        return iter(self._plugins)

    def __len__(self) -> int:
        return len(self._plugins)

    def __getitem__(self, index: int) -> PluginView:
        return self._plugins[index]

    @property
    def names(self) -> list[str]:
        return [p.name for p in self._plugins]

    @property
    def capabilities(self) -> list[str]:
        """Sorted, de-duplicated names of every capability provided across the
        collection — the single discovery surface for "what is available".

        For *who* provides one, filter: ``collection.capability.provides(name)``.
        """
        return sorted({cap for p in self._plugins for cap in p.provides})

    @property
    def count(self) -> int:
        return len(self._plugins)
