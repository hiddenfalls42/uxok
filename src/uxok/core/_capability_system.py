"""Capability system for kernel-style plugin dependencies."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any

from uxok.core._shared_utils import (
    format_capability_error,
    log_op,
)
from uxok.errors import CapabilityAccessError, MissingCapabilityError, PluginError
from uxok.registry._plugin_view import CapabilityInfo
from uxok.utils import derive_capability_name, get_protocol_methods
from uxok.utils._capability_utils import signature_incompatibility

if TYPE_CHECKING:
    from uxok.protocols._types import PluginId
    from uxok.protocols.core import AdmissionResult

logger = logging.getLogger(__name__)


@cache
def _leak_types() -> tuple[type, ...]:
    """Authority-handle types a sealed capability return must never carry.

    Resolved lazily (and memoized) to avoid an import cycle: ``Plugin`` lives in
    ``plugin/`` and the kernel handles in ``core/``, both of which import this
    module's package. By first call (a sealed return) every module is loaded.
    """
    from uxok.core._core import Core
    from uxok.core._core_facet import CoreFacet, LifecycleFacet
    from uxok.plugin._base import Plugin

    return (Plugin, Core, CoreFacet, LifecycleFacet)


# Reserved capabilities the kernel grants directly (no provider plugin). Declaring one in
# `requires` is always satisfiable and adds no dependency edge.
#   - `kernel.lifecycle` (RFC 0001 §3.2.2 / §2d): resolution is intercepted in
#     `Core.get_capability`, which returns a graph-control facet.
#   - `kernel.dispatch` (RFC 0002 §3.4): a pure authorization grant — it backs NO facet
#     and is never itself resolved. Holding it lets `enforce_requires` authorize resolving
#     any capability by name (control planes / dispatchers).
RESERVED_CAPABILITIES = frozenset({"kernel.lifecycle", "kernel.dispatch"})


@dataclass
class CapabilityPolicy:
    """Policy settings for capability resolution."""

    capability_collision: str
    capability_selection: str
    capability_missing: str
    capability_access: str = "open"


class CapabilitySystem:
    """Manages capability registration and resolution for plugins."""

    def __init__(self, policy: CapabilityPolicy) -> None:
        """Initialize capability system with policy.

        Args:
            policy: Capability policy settings (collision/selection/missing)
        """
        # Dict mapping capability name to list of providers (ordered by registration time)
        self._capabilities: dict[str, list[Any]] = {}
        # Dict mapping capability name to Protocol type (when typed capabilities are used)
        self._protocol_types: dict[str, type] = {}
        # INVARIANT (lock-free by design): every read-modify-write of
        # capability state is a synchronous critical section — no await may
        # appear inside one. Under cooperative asyncio that makes each section
        # atomic without locks (decision record #12). If a future change must
        # await inside a mutation, reintroduce a lock around that section.
        self._policy = policy

    _VALID_COLLISION_POLICIES = frozenset(
        {"error_on_conflict", "last_wins_with_warning", "first_wins"}
    )

    def _select_provider(self, providers: list[Any]) -> Any:
        """Select a provider from a list using the configured selection policy."""
        if self._policy.capability_selection == "last_registered":
            return providers[-1]
        return providers[0]

    def _resolve_collision_policy(self) -> str:
        """Return the active collision policy, defaulting unknown values safely."""
        policy = self._policy.capability_collision
        if policy not in self._VALID_COLLISION_POLICIES:
            logger.warning(
                "Unknown capability_collision policy %r; defaulting to error_on_conflict",
                policy,
            )
            return "error_on_conflict"
        return policy

    def _collision_error(self, capability: str, providers: list[Any]) -> PluginError:
        """Build a consistent collision PluginError for a capability."""
        names = ", ".join(sorted(p.metadata.name for p in providers))
        return PluginError(
            f"Capability '{capability}' is already provided by: {names} "
            f"(capability_collision policy is 'error_on_conflict')"
        )

    def _protocol_contract_violation(self, plugin: Any, protocol: type) -> str | None:
        """Return why ``plugin`` fails the ``protocol`` contract, or ``None``.

        Pure synchronous read — the single predicate behind both the raising
        :meth:`_validate_protocol_contract` and the non-raising admission probe
        (:meth:`contract_failures`). Mutates nothing.

        Uses structural checking (method-by-method) rather than isinstance()
        to avoid fragility with Python's runtime_checkable Protocol internals.
        Each protocol method must be present AND signature-compatible with the
        provider's method (see ``signature_incompatibility`` for the exact rule:
        the provider accepts every declared parameter, requires nothing the
        protocol does not supply, and agrees on the return annotation when both
        sides give one). Methods whose signature cannot be introspected fall
        back to presence-only.
        """
        missing_methods: list[str] = []
        incompatible: list[str] = []
        for attr_name in dir(protocol):
            if attr_name.startswith("_"):
                continue
            proto_attr = getattr(protocol, attr_name, None)
            if proto_attr is None or not callable(proto_attr):
                continue
            plugin_attr = getattr(plugin, attr_name, None)
            if plugin_attr is None or not callable(plugin_attr):
                missing_methods.append(attr_name)
                continue
            reason = signature_incompatibility(proto_attr, plugin_attr)
            if reason is not None:
                incompatible.append(f"{attr_name} ({reason})")

        if not missing_methods and not incompatible:
            return None

        detail_parts: list[str] = []
        if missing_methods:
            detail_parts.append(f"Missing methods: {', '.join(sorted(missing_methods))}")
        if incompatible:
            detail_parts.append(f"Incompatible methods: {', '.join(sorted(incompatible))}")
        return " ".join(detail_parts)

    def _validate_protocol_contract(self, plugin: Any, capability: str, protocol: type) -> None:
        """Raise if ``plugin`` does not implement the ``capability`` protocol contract.

        Thin raising wrapper over :meth:`_protocol_contract_violation` (the shared
        predicate), so the admission probe and this enforcer never drift.

        Raises:
            PluginError: If the plugin is missing a protocol method or implements
                one with an incompatible signature.
        """
        violation = self._protocol_contract_violation(plugin, protocol)
        if violation is not None:
            raise PluginError(
                f"Plugin '{plugin.metadata.name}' declares it provides '{capability}' "
                f"but does not implement the {protocol.__name__} protocol. " + violation
            )

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
        protocol: type | None = None
        if isinstance(capability, type):
            protocol = capability
            capability = derive_capability_name(capability)

        if capability not in self._capabilities or not self._capabilities[capability]:
            policy = self._policy.capability_missing
            if policy == "return_none":
                return None
            available = sorted(name for name, plugins in self._capabilities.items() if plugins)
            raise KeyError(format_capability_error(capability, available))

        providers = self._capabilities[capability]

        if tag is not None:
            filtered = [p for p in providers if tag in p.metadata.tags]
            if not filtered:
                all_tags = sorted({t for p in providers for t in p.metadata.tags})
                raise KeyError(
                    f"No provider for capability '{capability}' has tag '{tag}'. "
                    f"Provider tags: {all_tags}"
                )
            providers = filtered

        provider = self._select_provider(providers)

        if protocol is not None:
            self._validate_protocol_contract(provider, capability, protocol)
            # Provider attenuation (RFC 0001 §3.3): under "sealed", a *typed*
            # resolution returns a facet exposing only the protocol surface,
            # re-resolving the live provider on each call (so it transparently
            # rebinds across a swap and raises StalePluginError after revoke).
            # Untyped resolutions have no protocol to attenuate to and return raw.
            if self._policy.capability_access == "sealed":
                from uxok.core._capability_facet import CapabilityFacet

                return CapabilityFacet(self, capability, protocol, tag)

        return provider

    def attenuate_return(self, value: Any, *, capability: str | None = None) -> Any:
        """Refuse a sealed capability method that returns a live authority handle.

        RFC 0004 §4 / spec 0005 §C. Under ``capability_access="sealed"`` a typed
        resolution returns a :class:`CapabilityFacet`, but the provider method
        behind it can still hand back a live ``Plugin`` or kernel handle
        (``Core``/``CoreFacet``/``LifecycleFacet``) — a second-hop authority the
        consumer's manifest never declared. Such a return is **refused (raised)**,
        not wrapped: this system holds only a policy (no registry/collection), so it
        cannot build a descriptive view, and a raised error is the louder, more
        catchable signal for a self-coding repair loop than a silent downgrade.

        Pass through (not authority leaks): data, dataclasses, primitives, the
        ambient event bus / hook system (RFC 0001 §2.3 — the consumer already holds
        them via its own ``CoreFacet``), and already-attenuated views
        (``PluginView``/``PluginCollection``/``CapabilityFacet``).

        Honesty bound — robustness, not a boundary. One hop only: an author who
        *wants* to leak can return ``[plugin]`` (a container dodges the
        ``isinstance``), a bound method/closure over the live plugin
        (``self.some_method``), or reach ``self._Plugin__core_real`` directly. This
        stops the dominant *accidental* "returned ``self`` / ``get_plugin(...)`` /
        ``self.core`` from a sealed method" bug, and nothing stronger. Synchronous
        single ``isinstance`` — no await, lock-free invariant (decision #12) holds.
        """
        if isinstance(value, _leak_types()):
            # No plugin identity is reachable at this seam, so plugin_name stays
            # empty rather than lying with the leaked type's name; the type is
            # in the message.
            raise CapabilityAccessError(
                capability or "<sealed capability>",
                "",
                message=(
                    f"Sealed capability {f'{capability!r} ' if capability else ''}returned a "
                    f"live authority handle ({type(value).__name__}) from a provider method. "
                    "This is a manifest-invisible second-hop authority leak and is refused — "
                    "return data, ids, or capability names instead (RFC 0004 §4 / spec 0005 §C)."
                ),
            )
        return value

    def _live_provider(self, capability: str, tag: str | None = None) -> Any | None:
        """Re-resolve the current provider for a capability (synchronous, lock-free
        read), applying the same tag filter and selection policy as resolution.

        Returns ``None`` when the capability has no provider — the signal a
        ``CapabilityFacet`` turns into ``StalePluginError``. Used only for live
        re-resolution by sealed-mode facets; the registry/capability table is
        authoritative, exactly like ``PluginView``'s live resolution.
        """
        providers = self._capabilities.get(capability)
        if not providers:
            return None
        if tag is not None:
            providers = [p for p in providers if tag in p.metadata.tags]
            if not providers:
                return None
        return self._select_provider(providers)

    # --- Admission predicates (RFC 0003 v2 / spec 0005 §A) ---------------------
    # Each is a pure synchronous read of live capability state: no await, no
    # mutation. They are the single source of truth for "would this candidate
    # admit", shared by the advisory probe (Core.check_plugin → Core._admit) and
    # the raising enforcers below — so the probe and the commit can never drift.

    def missing_requirements(self, plugin: Any) -> frozenset[str]:
        """Required capabilities with no live provider (reserved grants exempt)."""
        requires = getattr(plugin.metadata, "requires", None)
        if not requires:
            return frozenset()
        return frozenset(
            capability
            for capability in requires
            if capability not in RESERVED_CAPABILITIES and not self._capabilities.get(capability)
        )

    def provides_conflicts(self, plugin: Any) -> frozenset[str]:
        """Provided capabilities that collide with a live provider.

        Empty unless the active collision policy is ``error_on_conflict`` —
        tag-discriminated multi-providers under the other policies are not
        conflicts.
        """
        provides = getattr(plugin.metadata, "provides", None)
        if not provides or self._resolve_collision_policy() != "error_on_conflict":
            return frozenset()
        return frozenset(c for c in provides if self._capabilities.get(c))

    def contract_failures(self, plugin: Any) -> frozenset[str]:
        """Typed capabilities whose provider violates its protocol contract."""
        plugin_protocols: dict[str, type] = getattr(plugin, "_capability_protocols", {})
        return frozenset(
            cap_name
            for cap_name, protocol in plugin_protocols.items()
            if self._protocol_contract_violation(plugin, protocol) is not None
        )

    def raise_admission_error(self, plugin: Any, admission: AdmissionResult) -> None:
        """Raise the established registration error for a failed capability admission.

        Lets ``register_plugin`` reject from a single :class:`AdmissionResult`
        while preserving the exact exception types/messages the capability
        validators raise. Fault precedence matches the legacy commit order
        (missing → contract → collision). The caller handles ``id_conflict``.
        """
        if admission.missing_requires:
            raise MissingCapabilityError(
                sorted(admission.missing_requires),
                phase="register",
                available=sorted(self._capabilities),
                requirer=plugin.metadata.name,
            )
        if admission.contract_failures:
            cap = sorted(admission.contract_failures)[0]
            protocol = plugin._capability_protocols[cap]
            self._validate_protocol_contract(plugin, cap, protocol)
        if admission.provides_conflicts:
            cap = sorted(admission.provides_conflicts)[0]
            raise self._collision_error(cap, self._capabilities[cap])

    async def validate_requirements(self, plugin: Any) -> set[PluginId]:
        """Validate that plugin's required capabilities are available.

        Used by the commit/swap paths: raises on a missing requirement (via the
        shared :meth:`missing_requirements` predicate) and, when clean, returns
        the provider dependency-id edges the registry consumes. The admission
        probe reuses the same predicate but never collects dep-ids.

        Returns:
            Set of plugin IDs that provide required capabilities

        Raises:
            MissingCapabilityError: If required capabilities are not available
        """
        missing = self.missing_requirements(plugin)
        if missing:
            raise MissingCapabilityError(
                sorted(missing),
                phase="register",
                available=sorted(self._capabilities),
                requirer=plugin.metadata.name,
            )

        capability_dependencies: set[PluginId] = set()
        requires = getattr(plugin.metadata, "requires", None)
        if not requires:
            return capability_dependencies

        for capability in requires:
            if capability in RESERVED_CAPABILITIES:
                continue  # kernel-granted, no provider plugin to depend on
            selected = self._select_provider(self._capabilities[capability])
            capability_dependencies.add(selected.metadata.id)

        return capability_dependencies

    async def register_capabilities(self, plugin: Any) -> None:
        """Register capabilities provided by a plugin.

        Atomic: protocol contracts and (under ``error_on_conflict``) collisions
        are checked up front, so a conflict on a later capability never leaves
        an earlier one half-registered. Validates typed capability contracts
        when the plugin provides Protocol types via ``_capability_protocols``.

        Raises:
            PluginError: If capability collision policy is 'error_on_conflict'
                         or if the plugin fails protocol contract validation
        """
        if not hasattr(plugin.metadata, "provides"):
            return

        plugin_protocols: dict[str, type] = getattr(plugin, "_capability_protocols", {})

        # Validate protocol contracts before mutating any state.
        for cap_name, protocol in plugin_protocols.items():
            self._validate_protocol_contract(plugin, cap_name, protocol)

        policy = self._resolve_collision_policy()

        # Pre-flight collision check: reject before any mutation so the
        # operation is all-or-nothing. Shares the `provides_conflicts` predicate
        # with the admission probe (no drift).
        conflicts = self.provides_conflicts(plugin)
        if conflicts:
            capability = sorted(conflicts)[0]
            raise self._collision_error(capability, self._capabilities[capability])

        for capability in plugin.metadata.provides:
            if capability in plugin_protocols:
                self._protocol_types[capability] = plugin_protocols[capability]

            existing_list = self._capabilities.get(capability)
            if not existing_list:
                self._capabilities[capability] = [plugin]
                logger.info(
                    "Registered capability",
                    extra=log_op(
                        "capability.register",
                        capability=capability,
                        provider=plugin.metadata.name,
                        typed=capability in plugin_protocols,
                    ),
                )
                continue

            # Collision: error_on_conflict was already rejected above.
            if policy == "first_wins":
                logger.debug(
                    "Capability already provided, keeping existing provider",
                    extra=log_op(
                        "capability.keep_existing",
                        capability=capability,
                        existing_provider=existing_list[0].metadata.name,
                    ),
                )
                continue

            logger.warning(
                "Capability already provided, adding provider",
                extra={
                    "capability": capability,
                    "existing_provider": existing_list[0].metadata.name,
                    "new_provider": plugin.metadata.name,
                },
            )
            existing_list.append(plugin)

    async def list_capabilities(self) -> list[str]:
        """List all available capability names."""
        return list(self._capabilities.keys())

    async def get_capability_info(self, capability: str) -> dict | None:
        """Get detailed information about a capability.

        When a Protocol type is associated with the capability, the info dict
        includes a ``protocol`` key with the protocol's method signatures for
        agent introspection.
        """
        if capability not in self._capabilities or not self._capabilities[capability]:
            return None

        providers = self._capabilities[capability]
        selected = self._select_provider(providers)

        provider_info = [
            {
                "name": p.metadata.name,
                "id": str(p.metadata.id),
                "version": p.metadata.version,
                "description": p.metadata.description,
                "tags": sorted(p.metadata.tags),
            }
            for p in providers
        ]

        info: dict[str, Any] = {
            "name": capability,
            "selected_provider": selected.metadata.name,
            "selected_provider_id": str(selected.metadata.id),
            "selected_version": selected.metadata.version,
            "selected_description": selected.metadata.description,
            "all_providers": provider_info,
            "provider_count": len(providers),
            "typed": capability in self._protocol_types,
        }

        protocol = self._protocol_types.get(capability)
        if protocol is not None:
            info["protocol"] = {
                "name": protocol.__name__,
                "methods": get_protocol_methods(protocol),
            }

        return info

    async def unregister_capabilities_by_plugin(self, plugin_id: str) -> list[str]:
        """Unregister all capabilities provided by a specific plugin.

        Returns the names of capabilities whose **last** provider was this
        plugin — i.e. capabilities that are now fully revoked with no
        replacement. The caller publishes ``core.capability.revoked`` for each;
        publishing lives in the caller because this method is a synchronous
        mutation critical section (lock-free invariant, decision record #12)
        and must not ``await`` the event bus mid-mutation.
        """
        try:
            plugins_to_remove = []
            for cap_name, providers in self._capabilities.items():
                for provider in providers:
                    if str(provider.metadata.id) == plugin_id:
                        plugins_to_remove.append((cap_name, provider))

            revoked: list[str] = []
            for cap_name, provider in plugins_to_remove:
                providers = self._capabilities[cap_name]
                if provider in providers:
                    providers.remove(provider)
                    logger.info(
                        "Removed capability provider",
                        extra=log_op(
                            "capability.remove_provider",
                            capability=cap_name,
                            provider=provider.metadata.name,
                        ),
                    )
                    if not providers:
                        del self._capabilities[cap_name]
                        self._protocol_types.pop(cap_name, None)
                        revoked.append(cap_name)
                        logger.info(
                            "Unregistered capability",
                            extra=log_op("capability.unregister", capability=cap_name),
                        )
            return revoked
        except Exception as e:
            logger.warning(f"Error unregistering capabilities for plugin {plugin_id}: {e}")
            raise

    async def swap_provider(
        self, old_provider: Any, new_provider: Any
    ) -> list[tuple[str, str, str]]:
        """Atomically reconcile capability providers during a hot-reload swap.

        Both providers must share the same plugin ID. This is the single
        capability-system primitive for hot reload: it replaces the old
        instance in place for capabilities the new version still provides,
        inserts capabilities the new version adds, and removes capabilities
        the new version no longer provides — de-duplicating by plugin ID so
        repeated reloads cannot grow the provider lists.

        Returns ``(capability, old_provider_id, new_provider_id)`` tuples for
        capabilities whose existing provider instance was replaced in place
        (a true rebind), so the caller can publish ``core.capability.rebound``.
        Capabilities the new version *adds* are fresh registrations, not
        rebinds, and are not reported. Because both instances share the plugin
        ID, ``old_provider_id == new_provider_id`` — the event signals "the
        provider instance was replaced," not "a different plugin took over."
        Publishing lives in the caller to keep the event-bus ``await`` outside
        this synchronous mutation (lock-free invariant, decision record #12).

        Args:
            old_provider: Old plugin instance being replaced.
            new_provider: New plugin instance to install.

        Raises:
            ValueError: If providers have different IDs.
            PluginError: If the new provider fails protocol contract validation.
        """
        old_id = str(old_provider.metadata.id)
        new_id = str(new_provider.metadata.id)

        if old_id != new_id:
            raise ValueError(f"Cannot swap providers with different IDs: {old_id} != {new_id}")

        new_caps: set[str] = set(getattr(new_provider.metadata, "provides", set()))
        new_protocols: dict[str, type] = getattr(new_provider, "_capability_protocols", {})

        # Validate protocol contracts before mutating any state.
        for cap_name, protocol in new_protocols.items():
            self._validate_protocol_contract(new_provider, cap_name, protocol)

        # 1. Install the new instance for every capability it provides,
        #    replacing any same-ID entries in place (de-duplicated). Track
        #    capabilities where an old-id entry was actually replaced — those
        #    are the rebinds worth announcing.
        rebound: list[tuple[str, str, str]] = []
        for capability in new_caps:
            providers = self._capabilities.get(capability, [])
            rebuilt: list[Any] = []
            inserted = False
            replaced = False
            for provider in providers:
                if str(provider.metadata.id) == old_id:
                    replaced = True
                    if not inserted:
                        rebuilt.append(new_provider)
                        inserted = True
                    # Drop any duplicate same-ID entries.
                else:
                    rebuilt.append(provider)
            if not inserted:
                rebuilt.append(new_provider)
            self._capabilities[capability] = rebuilt

            if capability in new_protocols:
                self._protocol_types[capability] = new_protocols[capability]

            if replaced:
                rebound.append((capability, old_id, new_id))

        # 2. Remove the old instance from capabilities the new version
        #    no longer provides.
        for capability in list(self._capabilities.keys()):
            if capability in new_caps:
                continue
            providers = self._capabilities[capability]
            filtered = [p for p in providers if str(p.metadata.id) != old_id]
            if len(filtered) != len(providers):
                if filtered:
                    self._capabilities[capability] = filtered
                else:
                    del self._capabilities[capability]
                    self._protocol_types.pop(capability, None)

        logger.debug(
            "Swapped capability provider",
            extra=log_op(
                "capability.swap_provider",
                old_plugin=old_provider.metadata.name,
                new_plugin=new_provider.metadata.name,
                capabilities=sorted(new_caps),
            ),
        )

        return rebound

    async def drain_all(self) -> None:
        """Drain all capability registrations (for core shutdown)."""
        count = len(self._capabilities)
        self._capabilities.clear()
        self._protocol_types.clear()
        logger.debug(f"Drained {count} capabilities during core shutdown")

    def snapshot_capability_info(self) -> dict[str, CapabilityInfo]:
        """Return a ``CapabilityInfo`` snapshot of all currently registered capabilities.

        Called by ``PluginCollectionService`` at collection-rebuild time.  The
        snapshot is synchronous and safe to call from within the rebuild lock —
        it reads ``_capabilities`` and ``_protocol_types`` in a single pass
        without any awaits, preserving the lock-free invariant.

        Protocol types change only on register/unregister (same events that
        trigger a collection rebuild), so the snapshot is as fresh as the
        membership itself.
        """
        result: dict[str, CapabilityInfo] = {}
        for cap_name, providers in self._capabilities.items():
            if not providers:
                continue
            selected = self._select_provider(providers)
            provider_info = [
                {
                    "name": p.metadata.name,
                    "id": str(p.metadata.id),
                    "version": p.metadata.version,
                    "description": p.metadata.description,
                    "tags": sorted(p.metadata.tags),
                }
                for p in providers
            ]
            protocol = self._protocol_types.get(cap_name)
            typed = protocol is not None
            result[cap_name] = CapabilityInfo(
                name=cap_name,
                providers=provider_info,
                selected_provider=selected.metadata.name,
                provider_count=len(providers),
                typed=typed,
                protocol_name=protocol.__name__ if protocol is not None else "",
                protocol_methods=get_protocol_methods(protocol) if protocol is not None else [],
            )
        return result
