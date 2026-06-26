"""Hook system protocol definitions."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from uxok.protocols._types import HookName


def _accepts_tick_context(func: Callable[..., Any]) -> bool:
    """True if func can receive a _tick_context keyword argument."""
    try:
        params = inspect.signature(func).parameters
    except (ValueError, TypeError):
        return False
    if "_tick_context" in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


@dataclass(frozen=True, slots=True)
class Hook:
    """Type-safe hook implementation.

    Hooks are async callables registered with a name and priority.
    """

    name: HookName
    func: Callable[..., Any]
    priority: int = 0
    plugin_id: str = ""
    owner: object | None = None
    is_async: bool = False
    accepts_tick_context: bool = False

    def __init__(
        self,
        name: HookName,
        callback: Callable[..., Any],
        priority: int = 0,
        plugin_id: str = "",
        owner: object | None = None,
    ) -> None:
        """Initialize hook with name, callback, and priority.

        ``owner`` is the registering plugin instance, used for instance-scoped
        hot-reload cleanup (see ``HookSystem.unregister_owner_hooks``). It lets
        closure handlers be drained by identity, not just bound methods.
        """
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "func", callback)
        object.__setattr__(self, "priority", priority)
        object.__setattr__(self, "plugin_id", plugin_id)
        object.__setattr__(self, "owner", owner)
        # Detected once here so the hot execution path stays inspection-free.
        object.__setattr__(self, "is_async", inspect.iscoroutinefunction(callback))
        object.__setattr__(self, "accepts_tick_context", _accepts_tick_context(callback))

    async def __call__(self, *args: object, **kwargs: object) -> Any:
        """Execute the hook.

        Async hooks are awaited directly. Sync hooks run inline on the event
        loop — they execute in the caller's task, so dispatching them to a
        thread pool would inject scheduling jitter and risk pool exhaustion.
        Blocking work belongs in ``Plugin.create_background_task``, which runs
        as a tracked background task off the caller's critical path.
        """
        if self.is_async:
            return await self.func(*args, **kwargs)

        return self.func(*args, **kwargs)


@runtime_checkable
class HookSystem(Protocol):
    """Hook system protocol - immutable interface.

    Note: This protocol defines the low-level implementation. The primary public API
    is the bound hook() method available on Plugin instances, which delegates to
    this _execute() method internally.
    """

    async def register(
        self,
        name: HookName,
        callback: Callable[..., Any],
        *,
        priority: int = 0,
        plugin_id: str = "",
        owner: object | None = None,
    ) -> None:
        """Register a hook handler by name and callable (primitives-based).

        Builds the Hook value object internally; callers never construct Hook
        directly. The ``@hook`` decorator desugars to this method.

        Args:
            name: Hook name (must be a valid identifier, dot-separated segments)
            callback: The callable to invoke when the hook fires
            priority: Higher values run first (default 0)
            plugin_id: Plugin ID string for ownership tracking and bulk removal
            owner: Registering plugin instance, for instance-scoped hot-reload
                cleanup (drains closure handlers by identity, not just bound
                methods)
        """
        ...

    async def unregister(self, name: HookName, hook: Hook, priority: int | None = None) -> bool:
        """Unregister a specific hook.

        Args:
            name: Hook name
            hook: Hook callable to remove
            priority: Optional priority filter

        Returns:
            True if hook was unregistered
        """
        ...

    async def execute(
        self,
        name: HookName,
        *args: object,
        firstresult: bool = False,
        plugin_id: str = "",
        **kwargs: object,
    ) -> list[object] | object | None:
        """Execute all registered hooks for a name.

        Args:
            name: Hook name (exact global name, no auto-prefixing)
            *args: Positional arguments to pass to hooks
            firstresult: If True, return first non-None result and stop execution
            plugin_id: ID of the calling plugin (consumed for tracing; not
                forwarded to hooks)
            **kwargs: Keyword arguments to pass to hooks

        Returns:
            List of results (firstresult=False) or single result (firstresult=True)

        Note:
            Use Plugin.hook() method for the clean public API.
        """
        ...

    async def unregister_plugin_hooks(self, plugin_id: str) -> None:
        """Remove all hooks registered by a plugin and clear its caches.

        Used by the ID-wide drain at unregistration.

        Args:
            plugin_id: ID (string form) whose hooks should be removed
        """
        ...

    async def unregister_owner_hooks(self, owner: object) -> None:
        """Remove hooks whose handler is a bound method of a specific instance.

        Used by hot reload: the old and new plugin instances share a plugin
        ID, so the swap must drain by instance identity, not by ID.

        Args:
            owner: The plugin instance whose hooks should be removed
        """
        ...

    async def precache_hooks(self, hook_names: list[str] | None = None) -> None:
        """Precache specific hooks or all hooks for optimal performance.

        Forces sorting and caching of hook handlers to eliminate first-call delay.
        Call after plugin registration to optimize hook execution performance.

        Args:
            hook_names: Specific hook names to precache, or None for all hooks
        """
        ...
