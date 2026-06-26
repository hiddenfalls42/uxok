"""Main hook system implementation with registration and management."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from uxok.hooks._cache import HookCache
from uxok.protocols import CoreConfig
from uxok.protocols._types import HookName
from uxok.protocols.events import EventBus
from uxok.protocols.hooks import Hook
from uxok.utils import validate_identifier

logger = logging.getLogger(__name__)


class _HookSystem:
    """Hook system with direct-await execution.

    Hook handlers run priority-sorted and serially inside each execute() call,
    in the caller's own asyncio task. Concurrency between independent callers
    is natural: different tasks each calling execute() run their handler chains
    concurrently with respect to each other.
    """

    def __init__(
        self,
        config: CoreConfig | None = None,
        event_bus: EventBus | None = None,
        clock: Any | None = None,
    ) -> None:
        """Initialize hook system."""
        self._config = config or CoreConfig()
        self._event_bus = event_bus
        self._clock = clock

        self._hooks: dict[HookName, list[tuple[int, Hook]]] = {}
        self._cache = HookCache()

    def _sort_hooks(self, hooks: list[tuple[int, Hook]]) -> list[tuple[int, Hook]]:
        """Sort hooks by priority (higher priority first)."""
        return sorted(hooks, key=lambda x: x[0], reverse=True)

    async def _execute_hook_safe(
        self,
        hook: Hook,
        args: tuple,
        kwargs: dict,
        tick_context: dict | None = None,
    ) -> Any:
        """Execute a single hook with error isolation.

        Tick context is passed only to hooks that declare _tick_context or
        **kwargs; an explicit caller-supplied _tick_context takes precedence.
        Failures are isolated to a None result and published as a
        core.hook_error event so callers can observe them.
        """
        if tick_context is not None and hook.accepts_tick_context and "_tick_context" not in kwargs:
            kwargs = {**kwargs, "_tick_context": tick_context}

        try:
            # Execute hook (handles async/sync detection)
            return await hook(*args, **kwargs)

        except Exception as e:
            logger.error(
                "Hook execution failed",
                extra={
                    "hook_name": getattr(hook, "name", "unknown"),
                    "priority": getattr(hook, "priority", 0),
                    "plugin_id": getattr(hook, "plugin_id", "unknown"),
                    "error": str(e),
                },
                exc_info=True,
            )
            await self._publish_hook_error(hook, e)
            return None

    async def _publish_hook_error(self, hook: Hook, error: Exception) -> None:
        """Publish a core.hook_error event so failures are observable."""
        if self._event_bus is None:
            return
        try:
            from uxok.protocols.events import Event

            await self._event_bus.publish(
                Event(
                    "core.hook_error",
                    {
                        "hook_name": getattr(hook, "name", "unknown"),
                        "plugin_id": getattr(hook, "plugin_id", ""),
                        "error": str(error),
                        "error_type": type(error).__name__,
                    },
                )
            )
        except Exception:
            logger.debug("Failed to publish core.hook_error event", exc_info=True)

    async def register(
        self,
        name: str,
        callback: Callable[..., Any],
        *,
        priority: int = 0,
        plugin_id: str = "",
        owner: object | None = None,
    ) -> None:
        """Register a hook handler by name and callable (primitives-based).

        Builds the Hook value object internally. The ``@hook`` decorator
        desugars to this method.

        Args:
            name: Hook name (must be a valid identifier, dot-separated segments)
            callback: The callable to invoke when the hook fires
            priority: Higher values run first (default 0)
            plugin_id: Plugin ID string for ownership tracking and bulk removal
            owner: Registering plugin instance, for instance-scoped hot-reload
                cleanup (drains closure handlers by identity, not just bound methods)
        """
        # Validate hook name
        try:
            validate_identifier(name, "hook_name")
        except Exception as e:
            raise ValueError(f"Invalid hook name: {name}") from e

        # Validate hook callable
        if not callable(callback):
            raise ValueError("Hook must be callable")

        hook = Hook(
            name=name, callback=callback, priority=priority, plugin_id=plugin_id, owner=owner
        )

        # Get current hooks and add new one
        current_hooks = self._hooks.get(name, [])
        updated_hooks = [*current_hooks, (priority, hook)]
        self._hooks[name] = updated_hooks

        # Invalidate cache for this hook name
        self._cache.invalidate_cache(name)

    async def execute(
        self,
        name: HookName,
        *args: object,
        firstresult: bool = False,
        plugin_id: str = "",
        **kwargs: object,
    ) -> list[object] | object | None:
        """Execute all registered hooks for a given name in the caller's task.

        Handlers run directly in the caller's task: priority-sorted serially
        inside ``_execute_now``.

        Args:
            name: Hook name
            *args: Positional arguments passed to each hook
            firstresult: If True, return first non-None result
            plugin_id: ID of calling plugin (for tracing)
            **kwargs: Keyword arguments passed to each hook

        Returns:
            List of results (firstresult=False) or single result (firstresult=True)
        """
        return await self._execute_now(
            name,
            *args,
            firstresult=firstresult,
            plugin_id=plugin_id,
            **kwargs,
        )

    async def _execute_now(
        self,
        name: HookName,
        *args: object,
        firstresult: bool = False,
        plugin_id: str = "",  # noqa: ARG002 — caller identity, kept for tracing
        **kwargs: object,
    ) -> list[object] | object | None:
        """Actual hook execution logic, runs within a tick boundary."""
        # Tick context is offered per-hook: only handlers that declare
        # _tick_context (or **kwargs) receive it. Injecting it into the shared
        # kwargs would TypeError every plain-signature handler.
        tick_context: dict[str, int] | None = None
        if self._clock is not None and self._clock.tick > 0:
            tick_context = {
                "tick": self._clock.tick,
                "slip": self._clock.slip,
            }

        cached_hooks = self._cache.get_cached_hooks(name)
        if cached_hooks is None:
            unsorted_hooks = self._hooks.get(name, [])
            sorted_hooks = self._sort_hooks(unsorted_hooks)
            self._cache.cache_hooks(name, sorted_hooks)
            cached_hooks = sorted_hooks

        # Snapshot the chain at execute-start. Registration/unregistration during
        # firing replaces self._hooks[name] and invalidates the cache without
        # mutating this list, so a removed hook still fires this round and changes
        # take effect on the next execute (atomic-frame property).
        hooks_snapshot = tuple(cached_hooks)

        results: list[Any] = []
        for _, hook in hooks_snapshot:
            result = await self._execute_hook_safe(hook, args, kwargs, tick_context)

            if firstresult and result is not None:
                return result
            if not firstresult:
                results.append(result)

        return results if not firstresult else None

    async def unregister_plugin_hooks(self, plugin_id: str) -> None:
        """Remove all hooks registered by a plugin and clear its caches."""
        self._remove_hooks(lambda h: getattr(h, "plugin_id", "") == plugin_id)

    async def unregister_owner_hooks(self, owner: object) -> None:
        """Remove hooks belonging to a specific plugin instance.

        Used by hot reload: the old and new plugin instances share a plugin
        ID, so the swap must drain by instance identity, not by ID. A hook is
        attributed to the instance by its explicit ``owner`` (set when
        registered via ``Plugin.register_hook``) or, failing that, by its
        handler being a bound method of the instance — so both closure and
        bound-method handlers drain correctly.
        """
        self._remove_hooks(lambda h: h.owner is owner or getattr(h.func, "__self__", None) is owner)

    def _remove_hooks(self, predicate: Callable[[Hook], bool]) -> None:
        """Remove all hooks matching predicate and invalidate affected caches."""
        names_to_invalidate: list[str] = []

        for hook_name, hooks_list in list(self._hooks.items()):
            filtered = [(p, h) for p, h in hooks_list if not predicate(h)]
            if len(filtered) != len(hooks_list):
                names_to_invalidate.append(hook_name)
                if filtered:
                    self._hooks[hook_name] = filtered
                else:
                    del self._hooks[hook_name]

        for hook_name in names_to_invalidate:
            self._cache.invalidate_cache(hook_name)

    async def unregister(
        self,
        name: HookName,
        hook: Hook,
        priority: int | None = None,
    ) -> bool:
        """Unregister a specific hook.

        Args:
            name: Hook name
            hook: Hook function to remove
            priority: Priority to match (optional for disambiguation)

        Returns:
            True if hook was found and removed
        """
        hooks_list = self._hooks.get(name, [])
        if not hooks_list:
            return False

        original_count = len(hooks_list)

        # Remove matching hooks (compare by function identity)
        def hook_matches(stored_hook: Hook, target_hook: Any) -> bool:
            """Check if stored hook matches target hook."""
            # Both should be Hook objects with func attribute
            if hasattr(stored_hook, "func") and hasattr(target_hook, "func"):
                stored_func = stored_hook.func
                target_func = target_hook.func
                # Handle bound methods - compare underlying function and instance
                if hasattr(stored_func, "__self__") and hasattr(target_func, "__self__"):
                    return (
                        stored_func.__func__ is target_func.__func__  # type: ignore[attr-defined]
                        and stored_func.__self__ is target_func.__self__
                    )
                return stored_func is target_func
            return stored_hook is target_hook

        if priority is not None:
            new_hooks = [
                (p, h) for p, h in hooks_list if not (p == priority and hook_matches(h, hook))
            ]
        else:
            new_hooks = [(p, h) for p, h in hooks_list if not hook_matches(h, hook)]

        removed = len(new_hooks) < original_count

        if removed:
            # Atomically update the hooks list
            if new_hooks:
                self._hooks[name] = new_hooks
            else:
                del self._hooks[name]

            # Invalidate cache
            self._cache.invalidate_cache(name)

        return removed

    async def get_hooks(self, name: HookName) -> tuple[tuple[int, Hook], ...]:
        """Get all registered hooks for a name (unsorted).

        Args:
            name: Hook name

        Returns:
            Immutable tuple of (priority, hook) tuples
        """
        hooks = self._hooks.get(name, [])
        return tuple(hooks)

    async def precache_hooks(self, hook_names: list[str] | None = None) -> None:
        """Precache specific hooks or all hooks to eliminate first-call delay.

        Forces sorting and caching of hook handlers immediately, ensuring
        O(1) execution on first call. Call after plugin registration to
        optimize performance.

        Args:
            hook_names: Specific hook names to precache, or None for all hooks

        Memory cost: ~300-500 bytes per hook handler
        Performance gain: Eliminates 50-200μs first-call sorting delay
        """
        if hook_names is None:
            hook_names = list(self._hooks.keys())

        for hook_name in hook_names:
            # Force the same caching logic used in _execute_now
            cached_hooks = self._cache.get_cached_hooks(hook_name)
            if cached_hooks is None:
                # Cache miss - sort hooks and cache them
                unsorted_hooks = self._hooks.get(hook_name, [])
                sorted_hooks = self._sort_hooks(unsorted_hooks)
                self._cache.cache_hooks(hook_name, sorted_hooks)

    async def clear_cache(self) -> None:
        """Clear all cached hooks."""
        self._cache.clear_cache()
