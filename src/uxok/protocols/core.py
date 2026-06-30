"""Core system protocol definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from uuid import UUID

    from uxok.protocols._types import CoreState, PluginId
    from uxok.protocols.config import CoreConfig
    from uxok.protocols.events import EventBus
    from uxok.protocols.hooks import HookSystem
    from uxok.protocols.plugin import PluginProtocol
    from uxok.registry._plugin_view import PluginCollection


@dataclass(frozen=True)
class AdmissionResult:
    """Verdict of an admission check (RFC 0003 v2 / spec 0005 §A).

    The faults a candidate plugin would raise against the *live* graph, computed
    without committing. Returned by :meth:`Core.check_plugin` (advisory) and the
    value the at-commit re-admission inside :meth:`Core.register_plugin` rejects
    on. ``ok`` is derived, so it can never disagree with the fault fields.

    Scope: this certifies the declared manifest *fits* the live graph, not that
    it is *complete* for what the plugin body resolves at runtime — under RFC
    0002 ``resolves`` is deliberately not registration-validated, so an
    under-declared ``resolves`` admits cleanly here and fails later as
    ``CapabilityAccessError``. "Admitted" means "fits the graph now."
    """

    missing_requires: frozenset[str] = field(default_factory=frozenset)
    """Load-order ``requires`` with no live provider (reserved grants exempt)."""
    id_conflict: bool = False
    """The candidate's id is already in the registry."""
    provides_conflicts: frozenset[str] = field(default_factory=frozenset)
    """Provided capabilities colliding with the live graph (under ``error_on_conflict``)."""
    contract_failures: frozenset[str] = field(default_factory=frozenset)
    """Typed capabilities whose provider violates its protocol contract."""

    @property
    def ok(self) -> bool:
        """True iff the candidate has no admission fault."""
        return not (
            self.missing_requires
            or self.id_conflict
            or self.provides_conflicts
            or self.contract_failures
        )


@runtime_checkable
class Core(Protocol):
    """Immutable core system interface.

    This is the main entry point for the core system.
    Once implemented, this interface should never change.
    """

    # State Management

    # PluginProtocol Management
    async def register_plugin(self, plugin: PluginProtocol) -> bool:
        """Register a new plugin.

        Args:
            plugin: The plugin to register

        Returns:
            True if registered successfully

        Raises:
            PluginError: If plugin already exists or dependencies missing
        """
        ...

    async def check_plugin(self, candidate: PluginProtocol) -> AdmissionResult:
        """Advisory, side-effect-free admission probe (RFC 0003 v2 / spec 0005 §A).

        Validate a candidate against the live plugin graph WITHOUT committing —
        returns an :class:`AdmissionResult` reporting the four capability/identity
        admission faults and mutates nothing (no registration, no ``start()``, no
        ``plugin.registered`` hook, no events). The pre-flight for
        write→check→repair loops.

        Advisory because it takes no lifecycle lock: the verdict describes the
        graph at call time and a concurrent registration can change it before you
        act. For the guarantee, call :meth:`register_plugin` — the same admission
        runs atomically under the lock at commit. A clean verdict means the
        candidate fits the graph now, not that commit will succeed: name conflicts,
        ``max_plugins``, blocked names, and dependency-graph faults are enforced
        only at commit, and ``resolves`` completeness is never registration-checked.
        """
        ...

    async def load_plugin(self, code: str) -> bool:
        """Load or reload a plugin from a code string.

        Accepts plugin source code from any origin — file, network, database,
        generated code, etc. The framework handles all internals:

          - Executes code in an isolated module (no sys.modules pollution)
          - Discovers the Plugin subclass
          - Instantiates with just (core) — no external arguments needed
          - If a plugin with the same name already exists → zero-downtime swap
          - If no plugin with that name exists → fresh registration

        Plugin config is supplied via core._plugin_configs (set at Core
        construction via CoreConfig.plugin_configs), not constructor arguments.

        Args:
            code: Python source code containing exactly one Plugin subclass.

        Returns:
            True if the plugin was successfully loaded or reloaded.

        Raises:
            PluginError: If no Plugin subclass is found, or loading fails.
        """
        ...

    async def unregister_plugin(self, plugin_id: PluginId, *, force: bool = False) -> bool:
        """Unregister a plugin.

        Args:
            plugin_id: ID of the plugin to unregister
            force: If True, allow removal even when dependents exist (hot-reload)

        Returns:
            True if unregistered successfully

        Raises:
            PluginError: If plugin doesn't exist or has dependents
        """
        ...

    # Introspection

    async def list(self) -> PluginCollection:
        """List all plugins with comprehensive information and composable filtering.

        Returns:
            PluginCollection with complete plugin ecosystem information
        """
        ...

    async def get_capability(self, capability: str | type, *, tag: str | None = None) -> Any:
        """Get plugin providing a capability.

        Accepts either a string name or a Protocol type.

        Args:
            capability: Capability name (str) or Protocol type
            tag: Optional tag to select a specific provider when multiple
                 plugins provide the same capability

        Returns:
            PluginProtocol providing the requested capability

        Raises:
            CapabilityError: If capability is not available
        """
        ...

    # Properties (sync accessors for core subsystems)
    @property
    def state(self) -> CoreState:
        """Get current core state.

        Returns:
            Current state
        """
        ...

    @property
    def events(self) -> EventBus:
        """Get event bus for publishing/subscribing to events.

        Returns:
            EventBus instance
        """
        ...

    @property
    def hooks(self) -> HookSystem:
        """Get hook system for extension points.

        Returns:
            HookSystem instance
        """
        ...

    @property
    def tick(self) -> int:
        """Get current tick number.

        Lock-free read of the tick counter. Returns 0 before core.start().

        Returns:
            Current tick number
        """
        ...

    @property
    def config(self) -> CoreConfig:
        """Get current core configuration.

        Returns:
            Current configuration (read-only)
        """
        ...

    @property
    def id(self) -> UUID:
        """Get unique core instance ID.

        Returns:
            Unique identifier for this core instance
        """
        ...
