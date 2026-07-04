"""Core system protocol definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterable
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


@dataclass(frozen=True, slots=True)
class SkippedSource:
    """One candidate that :meth:`Core.try_load_plugins` did not commit (RFC 0010 §4.1).

    Best-effort batch loading commits the maximal loadable subgraph and reports
    every excluded candidate here instead of raising. Each record names the
    source (as supplied), the plugin name if it materialized, a ``reason`` from
    the closed vocabulary below, and the originating exception (``None`` for
    reasons the planner synthesizes without one to raise).

    The ``reason`` vocabulary is closed — a host may branch on it exhaustively:

    - ``materialize_error`` — compile/exec/``__init__`` failed; ``name is None``.
    - ``duplicate_name`` — two in-batch candidates claim the same name; **all**
      claimants skip.
    - ``live_name_collision`` — the name is already live (batch loading is
      fresh-load-only; use :meth:`load_plugin` to hot-reload).
    - ``missing_capability`` — a required capability has no live provider and no
      surviving in-batch provider.
    - ``cycle_member`` — the candidate sits on a dependency cycle.
    - ``contract_failure`` — a provided typed capability violates its protocol.
    - ``duplicate_provider`` — under ``error_on_conflict``, the candidate's
      provided capability collides with a live provider or another in-batch
      candidate; every in-batch claimant skips (no winner is chosen).
    - ``max_plugins`` — the candidate fell beyond the ``max_plugins`` ceiling
      after the loadable subgraph was ordered.
    - ``dependent_of_skipped`` — transitively pruned: a requirement's only
      providers were themselves skipped (the ``cause`` names the blockers).
    - ``on_start_error`` — admission passed but commit raised (the candidate's
      ``on_start()`` or a TOCTOU re-detection under the lock).
    """

    origin: str | None
    """The source's ``origin`` as supplied; ``None`` for an anonymous source."""
    name: str | None
    """The plugin name, or ``None`` if the candidate never materialized."""
    reason: str
    """A code from the closed vocabulary above (RFC 0010 §4.2)."""
    cause: BaseException | None
    """The originating exception, or ``None`` for a synthesized planner verdict."""


@dataclass(frozen=True, slots=True)
class BatchLoadReport:
    """The outcome of :meth:`Core.try_load_plugins` (RFC 0010 §4.1).

    Best-effort batch loading never raises ``BatchLoadError``; it returns this
    report instead. ``loaded`` and ``skipped`` partition the input exactly: every
    source appears in one and only one (a materialize failure lands in
    ``skipped`` with ``name is None``), so ``len(loaded) + len(skipped)`` equals
    the number of sources supplied.
    """

    loaded: tuple[tuple[str, str | None], ...]
    """``(name, origin)`` pairs for committed plugins, in commit (topological) order."""
    skipped: tuple[SkippedSource, ...]
    """Excluded candidates, in input order."""


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
        ``max_plugins``, and dependency-graph faults are enforced
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

    async def load_plugins(self, sources: Iterable[tuple[str, str | None]]) -> tuple[str, ...]:
        """Boot a batch of plugin sources in dependency order, committed as one unit.

        Materializes every source, computes a topological commit order from
        the candidates' declared ``provides``/``requires`` plus already-live
        providers, then commits the whole plan under one hold of the
        lifecycle lock. Fresh-load-only: a source whose plugin name matches an
        already-live plugin is a plan-phase error — use :meth:`load_plugin` to
        hot-reload an existing plugin.

        Args:
            sources: ``(code, origin)`` pairs, one per plugin — the same shape
                as ``load_plugin``'s arguments. ``origin`` may be ``None``.

        Returns:
            Plugin names, in commit (topological) order. ``()`` for an empty
            ``sources``.

        Raises:
            CoreError: If the core is not in RUNNING state.
            BatchLoadError: If materializing, planning, or committing any
                source fails. ``phase`` discriminates a pre-commit graph fault
                (``"plan"``, ``installed == ()``) from a mid-batch commit
                failure (``"commit"``, ``installed`` is the live prefix).

        See :meth:`try_load_plugins` for the best-effort sibling that commits the
        maximal loadable subgraph and reports skips instead of raising.
        """
        ...

    async def try_load_plugins(self, sources: Iterable[tuple[str, str | None]]) -> BatchLoadReport:
        """Best-effort sibling of :meth:`load_plugins` (RFC 0010).

        Commits the **maximal loadable subgraph** and returns a
        :class:`BatchLoadReport` describing every committed and every excluded
        candidate — it never raises :class:`BatchLoadError`. Where
        :meth:`load_plugins` refuses the whole batch on the first statically
        decidable fault, this verb prunes the faulting candidate (and anything
        that transitively depends on it) and commits the rest.

        The same planner backs both verbs; they differ only in disposition
        (raise-first vs prune-and-commit). It never unregisters an already-live
        plugin — rollback stays host policy. A candidate that passes planning but
        whose commit raises is reported as ``on_start_error`` and its uncommitted
        dependents as ``dependent_of_skipped``; earlier commits stand.

        Args:
            sources: ``(code, origin)`` pairs, one per plugin — the same shape as
                :meth:`load_plugins`. ``origin`` may be ``None``.

        Returns:
            A :class:`BatchLoadReport` whose ``loaded`` (commit order) and
            ``skipped`` (input order) partition the input. ``BatchLoadReport((),
            ())`` for empty ``sources``.

        Raises:
            CoreError: If the core is not in RUNNING state.
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
