"""Plugin - unified base class with essential functionality and conveniences."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, TypeVar, overload
from uuid import uuid4

from uxok.plugin._decorators import discover_decorated_methods
from uxok.plugin._naming import detect_plugin_name, validate_plugin_name
from uxok.plugin.config_field import REQUIRED
from uxok.protocols import Core, Event, PluginMetadata, PluginProtocol
from uxok.protocols._types import PluginId
from uxok.utils import AsyncTaskManager, build_plugin_error_event, normalize_capability_set

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    # Concrete Core for the internal real-core reference (carries members the
    # protocol omits, e.g. `slip`); plugin-facing APIs still use the protocol `Core`.
    from uxok.core._core import Core as ConcreteCore

_T = TypeVar("_T")

logger = logging.getLogger(__name__)


class Plugin(PluginProtocol):
    """Base class for plugins with essential functionality and conveniences.

    This unified class provides:
    - Core reference injection
    - Lifecycle management
    - Convenience methods for events, config, hooks, capabilities
    - Decorator-based registration
    - Essential properties (metadata, core)

    Example:
        ```python
        class MyPlugin(Plugin):
            def __init__(self):
                super().__init__(
                    name="my_plugin",
                    version="1.0.0",
                    requires={"storage"},
                    provides={"processing"}
                )

            async def on_start(self):
                storage = await self.get_capability("storage")
                await storage.initialize()

            @hook("process.data", priority=10)
            async def process_data(self, data):
                result = await self._process(data)
                await self.emit("processed", result)
                return result
        ```
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        version: str = "0.0.1",
        description: str = "",
        author: str = "",
        requires: set[str] | frozenset[str] | None = None,
        resolves: set[str] | frozenset[str] | None = None,
        provides: set[str] | frozenset[str] | None = None,
        dependencies: set[PluginId] | frozenset[PluginId] | None = None,
        hooks_consumed: set[str] | frozenset[str] | None = None,
        events_published: set[str] | frozenset[str] | None = None,
        tags: set[str] | frozenset[str] | None = None,
        config_schema: dict[str, Any] | None = None,
    ) -> None:
        """Initialize plugin metadata.

        The kernel attaches the core *after* construction (at register/reload),
        so ``self.core`` is unavailable inside ``__init__`` — use ``on_start``
        for any setup that needs the kernel. See :meth:`_attach_core`.

        Args:
            name: Plugin name (auto-detected from class name if not provided)
            version: Plugin version string (default: "0.0.1")
            description: Plugin description (default: "")
            author: Plugin author (default: "")
            requires: Set of capability names this plugin requires (must exist at
                registration; hard load-order dependencies). Can include Protocol types.
            resolves: Set of capability names this plugin is authorized to resolve at
                runtime under ``capability_access="declared"``/``"sealed"`` (RFC 0002).
                Unlike ``requires``, these are NOT validated at registration — a name here
                may have no provider until one appears. Can include Protocol types.
            provides: Set of capability names this plugin provides (can include Protocol types)
            dependencies: Set of plugin IDs this plugin depends on
            hooks_consumed: Set of hook names this plugin consumes
            events_published: Set of event name patterns this plugin publishes
            tags: Set of tags describing this plugin's implementation characteristics
            config_schema: Declarative configuration schema with ConfigField objects

        Raises:
            TypeError: If unknown keyword arguments are passed (Python runtime enforcement)
            ValueError: If plugin name validation fails

        Example:
            ```python
            class MyPlugin(Plugin):
                def __init__(self):
                    super().__init__(
                        name="my_plugin",
                        version="1.0.0",
                        provides={"processing"}
                    )
            ```
        """
        # The core is attached by the kernel after construction (_attach_core);
        # it is deliberately NOT a constructor argument (RFC 0001 §3.2.3).

        # Auto-detect name from class name if not provided
        if name is None:
            name = detect_plugin_name(self.__class__.__name__)
        validate_plugin_name(name)

        # Normalize provides/requires/resolves: accept type objects alongside strings.
        # `resolves` (RFC 0002) is a runtime grant only — never validated at registration —
        # so its Protocol map is not retained (a typed sealed resolution derives the name
        # from the call-site protocol, not from metadata).
        provides_names, provides_protocols = normalize_capability_set(provides)
        requires_names, requires_protocols = normalize_capability_set(requires)
        resolves_names, _ = normalize_capability_set(resolves)

        # Store protocol mappings for registration-time validation
        self._capability_protocols: dict[str, type] = provides_protocols
        self._required_protocols: dict[str, type] = requires_protocols

        # Create metadata with sensible defaults (always string-based).
        # Identity is kernel-owned: every plugin gets a fresh unique id here;
        # authors cannot set it. On hot-reload the kernel transfers the old id
        # onto the new instance via _assign_id() (zero-downtime swap).
        self._metadata = PluginMetadata(
            id=uuid4(),
            name=name,
            version=version,
            description=description,
            author=author,
            dependencies=frozenset(dependencies or []),
            requires=requires_names,
            resolves=resolves_names,
            provides=provides_names,
            hooks_consumed=frozenset(hooks_consumed or []),
            events_published=frozenset(events_published or []),
            tags=frozenset(tags or []),
        )

        # Store config schema if provided
        self._config_schema: dict[str, Any] = config_schema or {}

        # Hook registration storage (name -> [(method, priority), ...])
        self._hooks: dict[str, list[tuple[Any, int]]] = {}

        # Event handler storage for decorator registration (pattern -> [info, ...])
        self._event_handlers: dict[str, Any] = {}

        # Background task tracking for cleanup. Failures are reported as
        # core.plugin_error events (source: background_task) for supervision.
        self._task_manager = AsyncTaskManager(error_reporter=self._report_task_failure)

        # Lifecycle state tracking
        self._initialized = False
        self._shutdown = False

        # Discover decorated methods (hooks, event handlers)
        hooks, event_handlers = discover_decorated_methods(self)
        self._hooks.update(hooks)
        self._event_handlers.update(event_handlers)

        # Create direct hook method (delegates to hook system)
        def hook_method(
            name: str,
            *args: Any,
            at_tick: int | None = None,
            firstresult: bool = False,
            **kwargs: Any,
        ) -> Any:
            """Execute hooks by name.

            Args:
                name: Hook name
                *args: Positional arguments passed to hooks
                at_tick: If provided, defer execution until this tick number.
                         Must be > core.tick at call time. Raises ValueError if
                         in the past. Returns None (fire-and-forget).
                firstresult: If True, return first non-None result immediately.
                **kwargs: Keyword arguments passed to hooks

            Returns:
                Hook results (list, or first non-None when firstresult=True),
                or None when at_tick is set (deferred; result unavailable).
            """
            if at_tick is not None:
                current_tick = self.__core_real.tick
                if at_tick <= current_tick:
                    raise ValueError(
                        f"at_tick={at_tick} is in the past (current tick={current_tick}). "
                        "Use core.tick + N for future scheduling."
                    )
                self.__core_real._tick_scheduler.schedule_at(  # type: ignore[attr-defined]
                    at_tick,
                    current_tick,
                    factory=lambda: self.__core_real.hooks.execute(
                        name,
                        *args,
                        firstresult=firstresult,
                        plugin_id=str(self._metadata.id),
                        **kwargs,
                    ),
                    owner=self,
                )
                return None
            return self.__core_real.hooks.execute(
                name,
                *args,
                firstresult=firstresult,
                plugin_id=str(self._metadata.id),
                **kwargs,
            )

        self.hook = hook_method

    # ========== Essential Properties ==========

    @property
    def metadata(self) -> PluginMetadata:
        """Get plugin metadata (immutable)."""
        return self._metadata

    def _assign_id(self, id: PluginId) -> None:  # noqa: A002 — mirrors metadata field name
        """Kernel-only: transfer identity onto this instance for a hot-reload swap.

        Plugin identity is kernel-assigned and unique per plugin; authors never
        set it. On reload the kernel rebinds the new instance to the old plugin's
        id so the swap is zero-downtime (subscriptions, capability registrations,
        and dependency edges keyed by id stay valid). Not a plugin-author API.
        """
        self._metadata = replace(self._metadata, id=id)

    def _attach_core(self, core: ConcreteCore) -> None:
        """Kernel-only: attach the core to this instance after construction.

        Called by the kernel at register/reload time, before ``start()`` (RFC
        0001 §3.2.3, decision D4). Stores two references, name-mangled for true
        privacy (the ``__state`` pattern):

        - ``__core_real`` — always the real :class:`Core`; used exclusively by
          base-class machinery (tick scheduling, config scoping, internal
          emit/hook plumbing, drain). Never handed to plugin code.
        - ``__core_view`` — what the public ``self.core`` property returns. Under
          ``capability_access="open"`` this is the real core (today's behavior).
          Under ``"declared"``/``"sealed"`` it is an attenuated ``CoreFacet`` so
          the plugin's authority over the kernel is bounded by its manifest.
        """
        self.__core_real = core
        mode = getattr(core.config, "capability_access", "open")
        if mode == "open":
            self.__core_view: Core = core
        else:
            from uxok.core._core_facet import CoreFacet

            self.__core_view = CoreFacet(core, self)  # type: ignore[assignment]

    @property
    def core(self) -> Core:
        """The plugin's view of the kernel.

        Under ``capability_access="open"`` this is the real :class:`Core`. Under
        ``"declared"``/``"sealed"`` it is an attenuated ``CoreFacet`` exposing
        only the plugin-safe surface (RFC 0001 §3.2.1). Available after the
        kernel attaches the core (register/start), not inside ``__init__``.
        """
        return self.__core_view

    # ========== Protocol Methods ==========

    async def start(self) -> None:
        """Start the plugin and register hooks/events."""
        if self._initialized:
            return

        if self._shutdown:
            raise RuntimeError("Cannot start plugin after shutdown")

        await self._register_handlers()

        # Validate config schema if declared
        self._validate_config_schema()

        # Call user initialization
        await self.on_start()

        # Record start time for accurate uptime tracking
        self._start_time = time.time()

        self._initialized = True
        logger.debug(
            "Plugin started successfully",
            extra={"plugin_id": str(self._metadata.id), "plugin_name": self._metadata.name},
        )

    async def _register_handlers(self) -> None:
        """Register decorated hooks and event handlers with the core systems."""
        # Register hooks — NO AUTO-PREFIXING on hook names.
        for hook_name, hook_list in self._hooks.items():
            for hook_method, priority in hook_list:
                await self.register_hook(hook_name, hook_method, priority=priority)

        # Register event handlers. Discovery stores a list per pattern;
        # a bare dict (user code assigning directly) is also accepted.
        for event_pattern, handler_infos in self._event_handlers.items():
            if isinstance(handler_infos, dict):
                handler_infos = [handler_infos]
            for handler_info in handler_infos:
                await self.subscribe(event_pattern, handler_info["method"])

    async def stop(self) -> None:
        """Stop the plugin and cleanup resources."""
        if not self._initialized or self._shutdown:
            return

        # Call user cleanup
        try:
            await self.on_stop()
        except Exception as e:
            logger.warning(
                "Error in plugin on_stop",
                extra={
                    "plugin_id": str(self._metadata.id),
                    "plugin_name": self._metadata.name,
                    "error": str(e),
                },
            )
            self._emit_plugin_error("lifecycle", e, phase="on_stop")

        # Use unified drain for consistent resource cleanup (protocol-compliant)
        try:
            from uxok.core._shared_utils import drain_plugin_resources

            await drain_plugin_resources(
                self._metadata.id,
                self,
                self.__core_real.events,
                self.__core_real.hooks,
                getattr(self.__core_real, "_capability_system", None),
                logger,
                scheduler=getattr(self.__core_real, "_tick_scheduler", None),
            )
        except Exception as e:
            logger.warning(
                "Error during unified resource drain",
                extra={
                    "plugin_id": str(self._metadata.id),
                    "plugin_name": self._metadata.name,
                    "error": str(e),
                },
            )

        self._shutdown = True
        logger.debug(
            "Plugin stopped successfully",
            extra={"plugin_id": str(self._metadata.id), "plugin_name": self._metadata.name},
        )

    # ========== Essential Lifecycle ==========

    async def on_start(self) -> None:
        """Override for initialization logic. Called when plugin starts."""

    async def on_stop(self) -> None:
        """Override for shutdown logic. Called when plugin stops."""

    # ========== Hot-Reload State Handoff ==========

    async def get_state(self) -> dict:
        """Serialize durable state for hot reload (and supervised restart).

        Called on the OLD instance before a swap. Return a plain dict of
        whatever must survive the reload — counters, buffers, identifiers.
        Keep it serializable: the contract is data, not live objects, so the
        new version never holds references into old code.

        Default: no state carries over.
        """
        return {}

    async def restore_state(self, state: dict) -> None:
        """Ingest state from the previous instance during hot reload.

        Called on the NEW instance after it has started (handlers registered,
        on_start complete) and before the old instance is drained. The dict is
        whatever the old version's get_state() returned — the new version is
        responsible for migrating shapes it no longer uses.

        Default: ignore incoming state.
        """

    def _validate_config_schema(self) -> None:
        """Validate plugin config against declared schema. Called at start()."""
        from uxok.errors import PluginError

        scoped = self.__core_real._plugin_configs.get(self._metadata.name, {})  # type: ignore[attr-defined]
        errors = []

        for key, field in self._config_schema.items():
            if key in scoped:
                value = scoped[key]
                if not isinstance(value, field.type):
                    errors.append(
                        f"  '{key}': expected {field.type.__name__}, got {type(value).__name__}"
                    )
            elif field.default is REQUIRED:
                desc = f" ({field.description})" if field.description else ""
                errors.append(f"  '{key}' is required but not supplied{desc}")

        if errors:
            raise PluginError(
                f"Plugin '{self._metadata.name}' config validation failed:\n" + "\n".join(errors)
            )

    # ========== Convenience Methods ==========

    async def emit(self, event_name: str, data: Any = None, *, at_tick: int | None = None) -> None:
        """Convenience: Publish an event.

        The event name is published verbatim — no prefix is added. Event.source
        is stamped with this plugin's name so subscribers can identify the emitter
        without encoding the name into the topic.

        For the immediate path (no ``at_tick``), the call is short-circuited
        before ``Event`` allocation when there are no subscribers (demand gate).

        Args:
            event_name: Event name (published exactly as given)
            data: Event payload data
            at_tick: If provided, defer execution until this tick number.
                     Must be > core.tick at call time. Raises ValueError if in the past.
        """
        if at_tick is not None:
            event = Event(event_name, data, source=self._metadata.name)
            current_tick = self.__core_real.tick
            # Validate immediately — fail fast
            if at_tick <= current_tick:
                raise ValueError(
                    f"at_tick={at_tick} is in the past (current tick={current_tick}). "
                    "Use core.tick + N for future scheduling."
                )
            # Schedule via TickScheduler
            self.__core_real._tick_scheduler.schedule_at(  # type: ignore[attr-defined]
                at_tick,
                current_tick,
                factory=lambda: self.__core_real.events.publish(event),
                owner=self,
            )
            return
        if not self.__core_real.events.has_subscribers(event_name):
            return
        await self.__core_real.events.publish(Event(event_name, data, source=self._metadata.name))

    def has_subscribers(self, event_name: str) -> bool:
        """True if someone is listening for event_name (mute-aware).

        Guard expensive payload construction:
        ``if self.has_subscribers(name): await self.emit(name, build_payload())``.
        """
        return self.__core_real.events.has_subscribers(event_name)

    async def subscribe(self, event_pattern: str, handler: Callable[..., Any]) -> None:
        """Subscribe to an event pattern dynamically.

        Public dynamic event subscription. The ``@event`` decorator desugars to
        this method. Subscriptions registered here are cleaned up automatically
        on stop/reload via the ``owner`` binding.

        Args:
            event_pattern: Event name or glob pattern (e.g. ``"user.*"``)
            handler: Callable invoked with the ``Event`` object when the pattern matches
        """
        await self.__core_real.events.subscribe(
            event_pattern, handler, self._metadata.id, owner=self
        )

    async def register_hook(
        self, hook_name: str, handler: Callable[..., Any], *, priority: int = 0
    ) -> None:
        """Register a hook handler dynamically.

        Public dynamic hook registration. The ``@hook`` decorator desugars to
        this method. Hooks registered here are cleaned up automatically on
        stop/reload via the ``owner`` binding, for any callable (including
        closures), symmetric with :meth:`subscribe`.

        Args:
            hook_name: Hook name (global — no auto-prefixing)
            handler: Callable invoked when the hook fires
            priority: Higher values run first (default 0)
        """
        await self.__core_real.hooks.register(
            hook_name, handler, priority=priority, plugin_id=str(self._metadata.id), owner=self
        )

    def config(self, key: str, default: Any = None) -> Any:
        """Convenience: Get configuration value.

        Lookup order:
          1. Plugin-scoped values (from core._plugin_configs[plugin_name])
          2. Schema default (if declared)
          3. Provided default argument

        Args:
            key: Configuration attribute name
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        # 1. Plugin-scoped namespace
        scoped = self.__core_real._plugin_configs.get(self._metadata.name, {})  # type: ignore[attr-defined]
        if key in scoped:
            return scoped[key]

        # 2. Schema default (if declared)
        if key in self._config_schema:
            field = self._config_schema[key]
            if field.default is not REQUIRED:
                return field.default

        return default

    @overload
    async def get_capability(self, capability: type[_T], *, tag: str | None = None) -> _T: ...

    @overload
    async def get_capability(self, capability: str, *, tag: str | None = None) -> Any: ...

    async def get_capability(self, capability: str | type, *, tag: str | None = None) -> Any:
        """Convenience: Get plugin providing a capability.

        Accepts either a string name or a Protocol type. When a Protocol type
        is passed, the return value is typed as that protocol for IDE support.

        Args:
            capability: Capability name (str) or Protocol type to resolve
            tag: Optional tag to select a specific provider when multiple
                 plugins provide the same capability

        Returns:
            PluginProtocol providing the requested capability

        Raises:
            CapabilityError: If capability is not available
            CapabilityAccessError: Under ``capability_access="declared"``/``"sealed"``,
                if this plugin did not declare the capability in ``requires``.

        Example:
            ```python
            # String-based (returns Any)
            greet = await self.get_capability("greeting")

            # Typed (returns Greeting, IDE autocomplete works)
            greet = await self.get_capability(Greeting)

            # Tag-filtered (select provider by tag)
            backend = await self.get_capability("inference_backend", tag="local")
            ```
        """
        # Consumer-side secure binding: gate on this plugin's declared `requires`
        # (RFC 0001 §3.2). The ergonomic `self.get_capability(...)` and the
        # `self.core.get_capability(...)` route (CoreFacet) enforce the same gate.
        from uxok.core._core_facet import enforce_requires

        mode = getattr(self.__core_real.config, "capability_access", "open")
        enforce_requires(capability, self, mode)
        # Forward the original (possibly typed) capability so sealed-mode attenuation
        # (§3.3) can see the protocol; the gate only needed the derived name.
        return await self.__core_real.get_capability(capability, tag=tag)

    async def create_background_task(
        self, coro: Coroutine[Any, Any, Any], name: str | None = None
    ) -> asyncio.Task:
        """Create and track a background task for this plugin."""
        return await self._task_manager.create_task(coro, name=name)

    def _report_task_failure(self, task: asyncio.Task) -> None:
        """Publish core.plugin_error for a crashed background task."""
        exc = task.exception()
        self._emit_plugin_error("background_task", exc, task_name=task.get_name())

    def _emit_plugin_error(
        self,
        source: str,
        error: BaseException | None,
        **extra: Any,
    ) -> None:
        """Fire-and-forget publication of a core.plugin_error event."""
        try:
            asyncio.get_running_loop().create_task(
                self.__core_real.events.publish(
                    build_plugin_error_event(
                        str(self._metadata.id),
                        self._metadata.name,
                        source,
                        error,
                        **extra,
                    )
                )
            )
        except Exception:
            logger.debug("Failed to publish core.plugin_error", exc_info=True)
