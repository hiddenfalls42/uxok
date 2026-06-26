"""Core orchestrator - direct plugin lifecycle management."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from uxok.core._capability_system import CapabilityPolicy, CapabilitySystem
from uxok.core._shared_utils import (
    drain_plugin_resources,
    format_plugin_error,
    log_op,
    resolve_plugin,
)
from uxok.core._state_manager import StateManager
from uxok.errors import CapabilityError, CoreError, PluginError
from uxok.events._bus import _EventBus
from uxok.hooks._system import _HookSystem
from uxok.protocols import (
    AdmissionResult,
    CoreConfig,
    CoreState,
    PluginProtocol,
)
from uxok.protocols import (
    Core as CoreProtocol,
)
from uxok.protocols._types import PluginId
from uxok.protocols.events import EventBus
from uxok.protocols.hooks import HookSystem
from uxok.protocols.registry import Registry
from uxok.timing._clock import TickClock
from uxok.timing._scheduler import TickScheduler

if TYPE_CHECKING:
    from uxok.registry._plugin_proxy import PluginCollection

from uxok.protocols.events import Event
from uxok.registry._plugin_collection_service import PluginCollectionService
from uxok.registry.impl import _Registry
from uxok.utils.async_primitives import _AsyncSafeSet

logger = logging.getLogger(__name__)


class _ReentrantLock:
    """Asyncio-compatible reentrant lock keyed by current task.

    Standard asyncio.Lock is not reentrant: a coroutine that acquires the lock
    and then awaits a callee that also tries to acquire it will deadlock. This
    happens in the lifecycle ops when Plugin.on_start() calls back into public
    methods like core.load_plugin() (e.g. PluginLoader scanning capabilities
    during on_start). The reentrant lock allows the same asyncio task to acquire
    it multiple times without blocking, using a depth counter.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task[Any] | None = None
        self._depth: int = 0

    async def __aenter__(self) -> _ReentrantLock:
        task = asyncio.current_task()
        if self._owner is task:
            self._depth += 1
            return self
        await self._lock.acquire()
        self._owner = task
        self._depth = 1
        return self

    async def __aexit__(self, *_: object) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()


class Core(CoreProtocol):
    """Immutable core system implementation.

    Implements the CoreProtocol interface defined in protocols.py.
    This is the heart of the framework - it never changes once shipped.
    All features are added via plugins.

    Features:
    - Plugin lifecycle management
    - Hook system with priorities
    - Event system with non-blocking dispatch
    - Configuration management
    - State management with recovery
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the core system.

        Accepts any ``CoreConfig`` field as a keyword argument; the kwargs are
        passed straight to ``CoreConfig``, which validates them. See
        ``CoreConfig`` for the full field set, defaults, and accepted values.
        """
        # Unique instance ID
        self._id = uuid4()

        # Parse kwargs into CoreConfig — validation happens in __post_init__
        self._core_config = CoreConfig(**kwargs)

        # Hook precaching state
        self._precaching_enabled = self._core_config.hook_precaching == "on_core_start"

        # Core subsystems
        self._event_bus: EventBus = _EventBus(self._core_config)
        self._hook_system: HookSystem = _HookSystem(
            self._core_config,
            self._event_bus,
        )
        self._registry: Registry = _Registry(max_plugins=self._core_config.max_plugins)

        self._tick_scheduler: TickScheduler = TickScheduler()
        self._tick_clock: TickClock = TickClock(
            tick_rate=self._core_config.tick_rate,
            scheduler=self._tick_scheduler,
            event_bus=self._event_bus,
            slip_threshold=self._core_config.tick_slip_threshold,
            precision=self._core_config.tick_precision,
            busy_wait_us=self._core_config.tick_busy_wait_us,
            catchup=self._core_config.tick_catchup,
        )

        # Inject clock into bus and hook system for tick stamping
        self._event_bus._clock = self._tick_clock  # type: ignore[attr-defined]
        self._hook_system._clock = self._tick_clock  # type: ignore[attr-defined]

        # Serializes the 4 await-spanning lifecycle ops (register/load/unregister/swap).
        # Reentrant so Plugin.on_start() can call back into public lifecycle methods
        # (e.g. PluginLoader calls core.load_plugin() per capability during on_start).
        # Synchronous (no-await) state mutations in registry/capability/subscriptions/hooks
        # are already atomic under cooperative asyncio — this lock covers only the
        # multi-step operations that span awaits.
        self._lifecycle_lock: _ReentrantLock = _ReentrantLock()

        # Apply blocked plugins from config BEFORE any concurrent access
        # This is safe because we're in __init__ before the core is exposed to concurrent use
        for plugin_name in self._core_config.blocked_plugins:
            self._registry._blocked_plugins.add(plugin_name)  # Direct access during init
            logger.info(f"Blocked plugin from config: {plugin_name}")

        # Capability system
        self._capability_system = CapabilitySystem(
            CapabilityPolicy(
                capability_collision=self._core_config.capability_collision,
                capability_selection=self._core_config.capability_selection,
                capability_missing=self._core_config.capability_missing,
                capability_access=self._core_config.capability_access,
            )
        )

        # State and collection
        self._state_manager = StateManager(self._id, self._event_bus, self._hook_system)
        self._collection_service = PluginCollectionService(
            self._registry,
            capability_snapshot_fn=self._capability_system.snapshot_capability_info,
        )

        # Concurrent operation guard (prevents double register/unregister on same plugin)
        self._active_operations: _AsyncSafeSet[PluginId] = _AsyncSafeSet()

        # Per-plugin configuration namespaces
        self._plugin_configs: dict[str, dict[str, Any]] = dict(self._core_config.plugin_configs)

        logger.info("Core initialized", extra={"core_id": str(self._id)})

    # ========== State Management ==========

    @property
    def state(self) -> CoreState:
        """Get current core state."""
        return self._state_manager.state

    @property
    def events(self) -> EventBus:
        """Get event bus for publishing/subscribing to events."""
        return self._event_bus

    @property
    def hooks(self) -> HookSystem:
        """Get hook system for extension points."""
        return self._hook_system

    @property
    def tick(self) -> int:
        """Current tick number. Lock-free read. Returns 0 before core.start()."""
        return self._tick_clock.tick

    @property
    def slip(self) -> int:
        """Current tick slip in periods. Lock-free read. Returns 0 before core.start()."""
        return self._tick_clock.slip

    async def start(self) -> None:
        """Start the core system.

        Transitions from INITIALIZED to RUNNING.

        Raises:
            CoreError: If not in INITIALIZED state
        """
        await self._state_manager.start()

        self._tick_clock.start()

        # Handle hook precaching on core start if configured
        if self._precaching_enabled:
            try:
                await self._precache_hooks()
            except Exception as e:
                logger.warning(
                    f"Hook precaching failed during core start: {e}",
                    extra={"error": str(e), "core_id": str(self._id)},
                )

    # ========== Plugin Management ==========

    async def register_plugin(self, plugin: PluginProtocol) -> bool:
        """Register a new plugin.

        Lifecycle operations are serialized by _lifecycle_lock to prevent
        concurrent register/load/unregister calls from racing across awaits.
        The per-plugin _active_operations guard remains for same-id reentrancy.

        Args:
            plugin: The plugin to register

        Returns:
            True if registered successfully, False if blocked

        Raises:
            PluginError: If registration fails
            MissingCapabilityError: If required capabilities are not available
        """
        async with self._lifecycle_lock:
            return await self._register_plugin_now(plugin)

    def _attach_core_to(self, plugin: PluginProtocol) -> None:
        """Attach this core to a plugin before it starts (RFC 0001 §3.2.3, D4).

        Coreless construction means the instance has no core until the kernel
        attaches one here. ``Plugin._attach_core`` decides — based on
        ``capability_access`` — whether the plugin sees the real core (``"open"``)
        or an attenuated ``CoreFacet`` (``"declared"``/``"sealed"``).
        """
        from uxok.plugin import Plugin

        if isinstance(plugin, Plugin):
            plugin._attach_core(self)

    async def _admit(self, candidate: PluginProtocol) -> AdmissionResult:
        """Pure admission check — read the live graph, raise nothing, mutate nothing.

        One routine, two callers: the advisory :meth:`check_plugin` (unlocked) and
        the at-commit re-admission in :meth:`_register_plugin_now` (under the
        lifecycle lock). Both verdicts come from the same capability predicates and
        the same registry read, so the probe and the commit can never drift —
        the check is the enforcer (RFC 0003 v2 / spec 0005 §A).

        Coherent snapshot: every read here is non-yielding — the capability
        predicates are synchronous and ``registry.contains`` awaits a coroutine
        with no internal await — so no concurrent mutation interleaves between the
        reads. It takes no lock and mutates nothing; that is precisely what makes a
        ``check_plugin`` verdict advisory (lock-free invariant, decision #12).
        """
        caps = self._capability_system
        return AdmissionResult(
            missing_requires=caps.missing_requirements(candidate),
            id_conflict=await self._registry.contains(candidate.metadata.id),
            provides_conflicts=caps.provides_conflicts(candidate),
            contract_failures=caps.contract_failures(candidate),
        )

    async def check_plugin(self, candidate: PluginProtocol) -> AdmissionResult:
        """Advisory, side-effect-free admission probe (RFC 0003 v2 / spec 0005 §A.2).

        Validate a candidate against the live plugin graph WITHOUT committing:
        returns an :class:`AdmissionResult` reporting the four capability/identity
        admission faults (missing requires, id conflict, provides collisions,
        protocol-contract failures) and mutates nothing — no registry change, no
        ``start()``, no ``plugin.registered`` hook, no events. The pre-flight for
        write→check→repair loops.

        Advisory because unlocked: it takes no lifecycle lock, so its verdict
        describes the graph at call time and a concurrent registration can change
        the graph before you act on it. For the guarantee, call
        :meth:`register_plugin` — the same admission runs atomically under the lock
        at commit.

        Scope boundary: a clean verdict means the candidate *fits the graph now*,
        not that :meth:`register_plugin` will succeed. Two classes of check are
        deliberately out of admission's vocabulary:

        - **Commit-only registry gates.** ``registry.add`` still rejects at commit
          for a **name** conflict (distinct from the modeled *id* conflict),
          ``max_plugins``, a blocked-plugin name, or a declared/circular dependency
          fault. These are not modeled here, so a candidate clean on the four
          admission faults can still fail commit.
        - **Authority completeness.** Admission certifies the *declared* manifest,
          not that it is *complete* for what the body resolves at runtime. Under
          RFC 0002, ``resolves`` is deliberately not registration-validated, so an
          under-declared ``resolves`` admits cleanly and fails later as
          ``CapabilityAccessError``.
        """
        return await self._admit(candidate)

    async def _register_plugin_now(self, plugin: PluginProtocol) -> bool:
        """Actual registration logic, runs within a tick boundary."""
        plugin_id = plugin.metadata.id

        # At-commit admission (RFC 0003 v2 / spec 0005 §A.3): the same check
        # `check_plugin` runs as an advisory probe is re-run here under the
        # lifecycle lock as the authoritative gate, closing the structural TOCTOU
        # window. Rejecting before any mutation also keeps the failure rollback
        # below from draining an already-registered, healthy plugin's live
        # subscriptions and hooks.
        admission = await self._admit(plugin)
        if not admission.ok:
            if admission.id_conflict:
                raise PluginError(
                    f"Plugin {plugin_id} is already registered; "
                    "use load_plugin() to hot-reload a running plugin"
                )
            self._capability_system.raise_admission_error(plugin, admission)

        if not await self._active_operations.add(plugin_id):
            raise PluginError(f"Plugin {plugin_id} already has an active operation")

        added_to_registry = False
        try:
            cap_deps = await self._capability_system.validate_requirements(plugin)

            added_to_registry = await self._registry.add(
                plugin, additional_dependencies=cap_deps or set()
            )
            if not added_to_registry:
                return False

            await self._capability_system.register_capabilities(plugin)
            self._attach_core_to(plugin)
            await plugin.start()
            await self._hook_system.execute("plugin.registered", plugin)

            self._collection_service.invalidate()
            await self._maybe_autostart_after_first_registration()

            logger.info(
                "Plugin registered",
                extra=log_op(
                    "register_plugin",
                    plugin_name=plugin.metadata.name,
                    plugin_id=str(plugin_id),
                ),
            )
            return True

        except Exception as e:
            with suppress(Exception):
                await self._event_bus.publish(
                    Event(
                        "core.plugin_error",
                        {
                            "plugin_id": str(plugin_id),
                            "plugin_name": plugin.metadata.name,
                            "source": "lifecycle",
                            "phase": "register",
                            "error": str(e),
                            "error_type": type(e).__name__,
                        },
                    )
                )
            with suppress(Exception):
                await drain_plugin_resources(
                    plugin_id,
                    plugin,
                    self._event_bus,
                    self._hook_system,
                    self._capability_system,
                    logger,
                    scheduler=self._tick_scheduler,
                    emit_revocation=False,
                )
            if added_to_registry:
                with suppress(Exception):
                    await self._registry.remove(plugin_id, force=True)
            raise
        finally:
            await self._active_operations.remove(plugin_id)

    async def _maybe_autostart_after_first_registration(self) -> None:
        """Auto-start core on the first successful plugin registration.

        Concurrent registrations may race here; StateManager's atomic
        transition lets exactly one win. Losing the race is success, not
        failure — the core is running either way, so the loser's (healthy)
        registration must not be rolled back.
        """
        if self.state != CoreState.INITIALIZED:
            return

        try:
            await self.start()
        except CoreError:
            if self.state == CoreState.RUNNING:
                return  # lost the auto-start race; another registration won
            raise

        logger.info(
            "Core auto-started on first plugin registration",
            extra={"core_id": str(self._id)},
        )

    async def load_plugin(self, code: str, origin: str | None = None) -> bool:
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

        Notes:
            - Only ``Plugin`` is injected into the execution namespace. Code
              using decorators must import them itself:
              ``from uxok import hook, event``.
            - The class is instantiated once to learn its name (load vs reload
              cannot be decided without it); on the reload path a second
              instance is built with the preserved ID. Plugin constructors
              should therefore be side-effect-free — acquire resources in
              ``on_start()``.

        Args:
            code: Python source code containing exactly one Plugin subclass.
            origin: Optional source file path. When given, the code is executed as
                a package rooted at the file's folder, so the plugin may import
                sibling helper modules relatively (``from . import _helper``) — a
                capability can fan out across files in its own subfolder. The
                synthetic package is registered in sys.modules only for the
                duration of execution, preserving the no-permanent-pollution
                invariant. When omitted, behaviour is unchanged (a bare isolated
                module).

        Returns:
            True if the plugin was successfully loaded or reloaded.

        Raises:
            PluginError: If no Plugin subclass is found, or loading fails.
        """
        import sys
        import types
        from pathlib import Path
        from uuid import uuid4

        # Import Plugin class so it's available in the isolated module
        from uxok.plugin import Plugin

        # Execute in an isolated module.  Default (no origin): a bare module that
        # never touches sys.modules.  With an ``origin`` file path: the module is
        # made a PACKAGE rooted at the file's folder, so the plugin can import
        # sibling helper modules relatively (``from . import _helper``).  The
        # synthetic package is registered in sys.modules only for the duration of
        # exec (so the import machinery can resolve siblings) and removed in the
        # finally — top-level imports are already bound into the module namespace,
        # so the loaded plugin keeps working and sys.modules stays clean.
        pkg_name = f"_orion_plugin_{uuid4().hex}"
        module = types.ModuleType(pkg_name)
        # Inject Plugin into the module namespace
        module.__dict__["Plugin"] = Plugin

        pkg_registered = False
        if origin is not None:
            origin_path = Path(origin)
            module.__file__ = str(origin_path)
            module.__path__ = [str(origin_path.parent)]  # makes the module a package
            module.__package__ = pkg_name  # relative imports resolve here
            sys.modules[pkg_name] = module
            pkg_registered = True

        try:
            exec(compile(code, origin or "<orion_plugin>", "exec"), module.__dict__)  # noqa: S102
        except Exception as e:
            raise PluginError(f"Failed to compile plugin code: {e}") from e
        finally:
            # Drop the synthetic package and any siblings it imported, keeping the
            # no-permanent-sys.modules-pollution invariant.
            if pkg_registered:
                for key in [
                    k for k in sys.modules if k == pkg_name or k.startswith(pkg_name + ".")
                ]:
                    sys.modules.pop(key, None)

        # Discover Plugin subclass
        plugin_classes = [
            obj
            for obj in vars(module).values()
            if isinstance(obj, type) and issubclass(obj, Plugin) and obj is not Plugin
        ]

        if not plugin_classes:
            raise PluginError("No Plugin subclass found in provided code")

        if len(plugin_classes) > 1:
            names = [cls.__name__ for cls in plugin_classes]
            raise PluginError(
                f"Multiple Plugin subclasses found: {names}. "
                "Each code string must contain exactly one Plugin subclass."
            )

        cls = plugin_classes[0]

        # Instantiate once to learn the plugin name (load vs reload cannot be
        # decided without it). Coreless construction (RFC 0001 §3.2.3): the
        # kernel attaches the core later via _attach_core, after the load/reload
        # branch is chosen and before the instance is started.
        temp_instance = cls()
        plugin_name = temp_instance.metadata.name

        # The existing-name lookup and the load/reload branch run as ONE locked
        # operation, so concurrent load_plugin calls for the same name are
        # serialized instead of racing the check.
        async with self._lifecycle_lock:
            await self._load_plugin_now(temp_instance, plugin_name)
        return True

    async def _load_plugin_now(self, temp_instance: PluginProtocol, plugin_name: str) -> None:
        """Atomic load-or-reload branch; runs within a tick boundary."""
        existing = None
        for plugin in (await self._registry.all()).values():
            if plugin.metadata.name == plugin_name:
                existing = plugin
                break

        if existing is not None:
            # RELOAD: transfer the old id onto the already-built instance for a
            # zero-downtime swap. Identity is kernel-owned, so we rebind here
            # rather than asking the plugin's __init__ to accept an id. The
            # coreless instance (RFC 0001 §3.2.3) is reloadable without any
            # constructor contract; the core is attached in the swap path before
            # start. Reusing temp_instance also avoids a second construction.
            from uxok.plugin import Plugin

            old_id = existing.metadata.id
            assert isinstance(temp_instance, Plugin)  # discovery guarantees this
            temp_instance._assign_id(old_id)
            await self._reload_plugin_now(existing, temp_instance)
            await self._event_bus.publish(
                Event(
                    "core.plugin_reloaded",
                    {
                        "plugin_name": plugin_name,
                        "old_id": str(old_id),
                        "new_id": str(temp_instance.metadata.id),
                    },
                )
            )
            logger.info("Plugin reloaded", extra=log_op("load_plugin", plugin_name=plugin_name))
        else:
            # FRESH LOAD: use the instance we already created
            await self._register_plugin_now(temp_instance)
            logger.info("Plugin loaded", extra=log_op("load_plugin", plugin_name=plugin_name))

    async def unregister_plugin(self, plugin_id: PluginId | str, *, force: bool = False) -> bool:
        """Unregister a plugin.

        Serialized by _lifecycle_lock for cross-op ordering.

        Args:
            plugin_id: ID or name of the plugin to unregister
            force: If True, allow removal even when other plugins depend on
                   this one (used internally during hot-reload).

        Returns:
            True if unregistered successfully, False if plugin not found
        """
        async with self._lifecycle_lock:
            return await self._unregister_plugin_now(plugin_id, force=force)

    async def _unregister_plugin_now(
        self, plugin_id: PluginId | str, *, force: bool = False
    ) -> bool:
        """Actual unregistration logic, runs within a tick boundary."""
        plugin, _resolved_id = await resolve_plugin(plugin_id, self._registry)
        if plugin is None:
            return False

        real_id = plugin.metadata.id
        plugin_name = plugin.metadata.name

        if not await self._active_operations.add(real_id):
            raise PluginError(f"Plugin {real_id} already has an active operation")

        if not force:
            dependents = await self._registry.dependents(real_id)
            active_dependents = [d for d in dependents if await self._registry.contains(d)]
            if active_dependents:
                names = []
                for dep_id in active_dependents:
                    dep_plugin = await self._registry.get(dep_id)
                    if dep_plugin:
                        names.append(dep_plugin.metadata.name)
                await self._active_operations.remove(real_id)
                raise PluginError(
                    format_plugin_error(
                        str(real_id),
                        f"dependents present -> {', '.join(names)}",
                    )
                )

        try:
            await plugin.stop()
            await self._registry.remove(real_id, force=force)
            await self._hook_system.execute("plugin.unregistered", real_id)

            self._collection_service.invalidate()

            logger.info(
                "Plugin unregistered",
                extra=log_op("unregister_plugin", plugin_name=plugin_name, plugin_id=str(real_id)),
            )
            return True

        except Exception:
            with suppress(Exception):
                await self._registry.remove(real_id, force=True)
            raise
        finally:
            with suppress(Exception):
                await drain_plugin_resources(
                    real_id,
                    plugin,
                    self._event_bus,
                    self._hook_system,
                    self._capability_system,
                    logger,
                    scheduler=self._tick_scheduler,
                )
            await self._active_operations.remove(real_id)

    async def get_plugin(self, plugin_id: PluginId | str) -> PluginProtocol | None:
        """Get a plugin by ID or name.

        Args:
            plugin_id: Plugin UUID, UUID string, or name string

        Returns:
            The plugin if found, None otherwise
        """
        plugin, _ = await resolve_plugin(plugin_id, self._registry)
        return plugin

    async def _precache_hooks(self) -> None:
        """Precache all registered hooks for optimal performance.

        Called internally based on CoreConfig.hook_precaching; not part of the
        public surface — first-call hook warming is a kernel concern.
        """
        try:
            await self._hook_system.precache_hooks()
            logger.debug("Hook precaching completed", extra={"core_id": str(self._id)})
        except Exception as e:
            logger.warning(
                f"Hook precaching failed: {e}",
                extra={"error": str(e), "core_id": str(self._id)},
            )

    # ========== Hot Reload ==========

    async def _reload_plugin_now(
        self, old_plugin: PluginProtocol, new_plugin: PluginProtocol
    ) -> None:
        """Reload logic, runs under the lifecycle lock and the operation guard.

        Mirrors `_register_plugin_now`: a hot-reload is a lifecycle operation and
        must be serialized through the lifecycle lock and protected by the same
        per-plugin operation guard as registration/unregistration.
        """
        plugin_id = old_plugin.metadata.id

        if not await self._active_operations.add(plugin_id):
            raise PluginError(f"Plugin {plugin_id} already has an active operation")
        try:
            await self._swap_plugin(old_plugin, new_plugin)
        finally:
            await self._active_operations.remove(plugin_id)

    async def _swap_plugin(self, old_plugin: PluginProtocol, new_plugin: PluginProtocol) -> None:
        """Atomically swap a plugin instance with zero downtime.

        This is a kernel primitive for hot reload (internal use only).
        The swap:
        1. Starts the new plugin (registers hooks and events)
        2. Atomically swaps instance in registry (preserves ID, deps, dependents)
        3. Reconciles capability providers (replace/add/remove, de-duplicated)
        4. Drains old plugin's hooks, events, and background tasks

        During the brief window between steps 1 and 4, both instances exist,
        but registry lookups return only the new instance. Capabilities remain
        available throughout with zero interruption.

        Args:
            old_plugin: Existing registered plugin instance to replace.
            new_plugin: New plugin instance. Must have the same name as old_plugin.

        Raises:
            PluginError: If old_plugin is not found, or names do not match.
        """
        old_id = old_plugin.metadata.id

        # 1. Fail fast if the new version's requirements aren't satisfiable,
        #    and compute its fresh dependency edges (declared + capability).
        cap_deps = await self._capability_system.validate_requirements(new_plugin)
        new_deps = set(new_plugin.metadata.dependencies) | (cap_deps or set())
        new_deps.discard(old_id)  # a plugin never depends on itself
        old_deps = await self._registry.dependencies(old_id)

        # 2. State handoff: capture from the old instance before any mutation.
        #    A get_state() failure aborts the reload with nothing to roll back.
        state = await old_plugin.get_state()

        registry_swapped = False
        try:
            # 3. Attach the core, then start the new plugin (registers hooks and
            #    events). Attach must precede start: on_start uses self.core.
            self._attach_core_to(new_plugin)
            await new_plugin.start()

            # 4. Swap in registry (atomic - preserves ID and dependents,
            #    replaces dependency edges with the new version's)
            await self._registry.swap_instance(old_id, new_plugin, dependencies=new_deps)
            registry_swapped = True

            # 5. Reconcile capability providers (in-place replace, de-duplicated)
            rebound = await self._capability_system.swap_provider(old_plugin, new_plugin)

            # 5a. Announce rebinds. The capability mutation above already
            #     completed synchronously, so this event-bus await is outside
            #     the critical section (lock-free invariant). A publish failure
            #     must never fail the reload.
            for capability, old_provider_id, new_provider_id in rebound:
                with suppress(Exception):
                    await self._event_bus.publish(
                        Event(
                            "core.capability.rebound",
                            {
                                "capability": capability,
                                "old_provider_id": old_provider_id,
                                "new_provider_id": new_provider_id,
                            },
                        )
                    )

            # 6. Hand the captured state to the new instance
            await new_plugin.restore_state(state)
        except Exception:
            # Roll back so the old version keeps running. Both instances share
            # the plugin ID, so the half-started new instance is drained by
            # INSTANCE identity — the old instance's registrations are never
            # touched.
            if registry_swapped:
                with suppress(Exception):
                    await self._registry.swap_instance(old_id, old_plugin, dependencies=old_deps)
            with suppress(Exception):
                await self._drain_instance(new_plugin)
            raise

        # 7. Call the old instance's on_stop so it can release external resources.
        #    on_stop is NOT part of PluginProtocol (protocols are immutable), so
        #    use a getattr guard. A raising on_stop must never fail the reload.
        on_stop = getattr(old_plugin, "on_stop", None)
        if on_stop is not None:
            try:
                await on_stop()
            except Exception as e:
                logger.warning(
                    "Error in plugin on_stop during hot reload",
                    extra={
                        "plugin_id": str(old_plugin.metadata.id),
                        "plugin_name": old_plugin.metadata.name,
                        "error": str(e),
                    },
                )
                with suppress(Exception):
                    await self._event_bus.publish(
                        Event(
                            "core.plugin_error",
                            {
                                "plugin_id": str(old_plugin.metadata.id),
                                "plugin_name": old_plugin.metadata.name,
                                "source": "lifecycle",
                                "error": str(e),
                                "error_type": type(e).__name__,
                                "phase": "on_stop",
                            },
                        )
                    )

        # Mark the old instance shut down so a stray later stop() on a retained
        # reference is a no-op: Plugin.stop() guards on _shutdown (not
        # name-mangled), so setting it here prevents a double on_stop call if
        # the caller holds a reference to the old instance after reload.
        if hasattr(old_plugin, "_shutdown"):
            old_plugin._shutdown = True  # type: ignore[union-attr]

        # 8. Drain the old instance — by instance identity, for the same
        #    shared-ID reason: an ID-wide drain would also destroy the new
        #    instance's just-registered hooks and subscriptions.
        await self._drain_instance(old_plugin)

        logger.debug(
            "Swapped plugin instance with zero downtime",
            extra=log_op(
                "swap_plugin",
                plugin_name=old_plugin.metadata.name,
                plugin_id=str(old_id),
            ),
        )

    async def _drain_instance(self, plugin: PluginProtocol) -> None:
        """Drain one plugin INSTANCE's resources, leaving same-ID siblings intact.

        Hot-reload companion to drain_plugin_resources: during a swap the old
        and new instances share a plugin ID, so cleanup must be scoped by
        instance identity. Hooks/subscriptions registered with closures that
        carry no instance ownership are left for the ID-wide drain at
        unregistration.
        """
        with suppress(Exception):
            await self._event_bus.unsubscribe_owner(plugin)
        with suppress(Exception):
            await self._hook_system.unregister_owner_hooks(plugin)
        with suppress(Exception):
            self._tick_scheduler.unschedule_owner(plugin)
        if hasattr(plugin, "_task_manager"):
            with suppress(Exception):
                await plugin._task_manager.cancel_all()

    async def list(self) -> PluginCollection:
        """List all plugins as PluginView objects.

        Returns a PluginCollection supporting both direct and composable
        filtering.  Views are descriptions, not handles: fresh descriptive
        fields (rebuilt on every registry change) plus benign live reads
        (``status``, ``ready``, ``uptime``, ``methods``). They expose no way to
        invoke or hand back a live instance (RFC 0001 §3.2.2).

        Returns:
            PluginCollection with complete plugin ecosystem information,
            including capability-protocol info via
            ``collection.capability.info(name)``.
        """
        return await self._collection_service.list()

    # ========== Sync Properties ==========

    @property
    def config(self) -> CoreConfig:
        """Get current core configuration (read-only)."""
        return self._core_config

    @property
    def id(self) -> UUID:
        """Get unique core instance ID."""
        return self._id

    # ========== Capability System ==========

    async def get_capability(self, capability: str | type, *, tag: str | None = None) -> Any:
        """Get plugin providing a capability.

        Accepts either a string name or a Protocol type. When a Protocol type
        is passed, the provider is validated against the protocol contract.

        Args:
            capability: Capability name (str) or Protocol type to resolve
            tag: Optional tag to select a specific provider when multiple
                 plugins provide the same capability

        Returns:
            PluginProtocol providing the requested capability

        Raises:
            CapabilityError: If capability is not available
            PluginError: If provider doesn't implement the protocol contract
        """
        # Reserved tier-2 grant (RFC 0001 §3.2.2 / §2d): the kernel "provides"
        # `kernel.lifecycle` with no plugin instance. Both resolution routes
        # (`self.get_capability` and the CoreFacet `self.core.get_capability`) funnel
        # here, so this single intercept covers them identically. The consumer-side
        # `requires` gate runs upstream in each route before delegating to this root.
        if capability == "kernel.lifecycle":
            from uxok.core._core_facet import LifecycleFacet

            return LifecycleFacet(self)
        try:
            return await self._capability_system.get_capability(capability, tag=tag)
        except KeyError:
            from uxok.utils import derive_capability_name

            cap_name = (
                derive_capability_name(capability) if isinstance(capability, type) else capability
            )
            available = await self._capability_system.list_capabilities()
            raise CapabilityError(cap_name, available) from None

    # ========== Lifecycle ==========

    async def stop(self) -> None:
        """Stop the core: full teardown leaving an empty, reusable core.

        Plugins are unregistered in reverse dependency order (dependents before
        dependencies), so plugins can safely use capabilities during their
        on_stop() handlers. After stop() the registry is empty; core.start()
        works again with a fresh plugin graph supplied by the caller. Plugin
        instances are one-shot — state continuity is explicit via
        get_state()/restore_state(), never by instance survival.

        Raises:
            CoreError: If not in a stoppable state
        """
        try:
            # STOPPING is the drain phase; teardown failure drives → FAILED.
            if not await self._state_manager.begin_stop():
                return

            await self._tick_clock.stop()
            # Drain in-flight event dispatch tasks and scheduled work tasks.
            await self._event_bus.drain()  # type: ignore[attr-defined]
            await self._tick_scheduler.cancel_all()

            # Teardown: fully unregister plugins in reverse dependency order.
            # stop() leaves an EMPTY, reusable core — plugin instances are
            # one-shot; the orchestrator rebuilds the graph from its factories
            # on restart. State continuity is always explicit
            # (get_state/restore_state), never by instance survival.
            ordered_ids = await self._registry.load_order()
            for plugin_id in reversed(ordered_ids):
                try:
                    await self._unregister_plugin_now(plugin_id, force=True)
                except Exception as e:
                    logger.warning(
                        "Failed to unregister plugin during teardown",
                        extra={"plugin_id": str(plugin_id), "error": str(e)},
                    )

            self._collection_service.invalidate()
            await self._capability_system.drain_all()
        except Exception as e:
            logger.warning(f"Core stop encountered error: {e}", extra={"core_id": str(self._id)})
            with suppress(Exception):
                await self._state_manager.fail()
            raise

        await self._state_manager.finish_stop()

    async def __aenter__(self) -> Core:
        """Async context manager entry - start the core."""
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Async context manager exit - stop the core."""
        await self.stop()
