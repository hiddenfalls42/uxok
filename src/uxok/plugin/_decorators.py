"""Decorators for declarative plugin development."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Marker attribute names
_HOOK_MARKER = "_orion_hooks"
_ON_HANDLER_MARKER = "_orion_event_handlers"


def hook(
    hook_name: str,
    priority: int = 0,
) -> Callable[[Callable], Callable]:
    """Decorator to mark a method as a hook handler (works at class level).

    This decorator can be used at class definition time. The actual registration
    happens during plugin initialization via method introspection.

    Args:
        hook_name: Name of the hook (registered globally - use explicit naming like "data.validate")
        priority: Hook priority (higher = executed first), default 0

    Example:
        ```python
        class MyPlugin(Plugin):
            @hook("data.process", priority=10)  # Global hook name
            async def process_data(self, data: dict) -> dict:
                return {"processed": True, **data}
        ```

    Note:
        This is hot-loading safe - each plugin instance discovers its own
        decorated methods, creating fresh bound methods for each instance.
        Conditional or staged execution belongs in handler code: an `if` at the
        top of the handler is a condition; sequential `self.hook(...)` calls are
        a pipeline.
    """

    def decorator(func: Callable) -> Callable:
        # Store hook metadata on the function object
        if not hasattr(func, _HOOK_MARKER):
            setattr(func, _HOOK_MARKER, [])
        getattr(func, _HOOK_MARKER).append(
            {
                "name": hook_name,
                "priority": priority,
            }
        )
        return func

    return decorator


def event(event_pattern: str) -> Callable[[Callable], Callable]:
    """Decorator to mark a method as an event handler (works at class level).

    This decorator can be used at class definition time. The actual subscription
    happens during plugin initialization via method introspection.

    Args:
        event_pattern: Event pattern to subscribe to (supports globs like "user.*")

    Example:
        ```python
        class MyPlugin(Plugin):
            @event("system.*")
            async def handle_system_events(self, event: Event) -> None:
                print(f"System event: {event.name}")
                user_id = event.data.get("user_id")
                action = event.data.get("action")
        ```

    Note:
        This is hot-loading safe - each plugin instance discovers its own
        decorated methods, creating fresh bound methods for each instance.
    """

    def decorator(func: Callable) -> Callable:
        # Store metadata on function object
        if not hasattr(func, _ON_HANDLER_MARKER):
            setattr(func, _ON_HANDLER_MARKER, [])

        getattr(func, _ON_HANDLER_MARKER).append({"pattern": event_pattern})
        return func

    return decorator


def discover_decorated_methods(
    instance: object,
) -> tuple[
    dict[str, list[tuple[Callable, int]]],
    dict[str, list[dict[str, Any]]],
]:
    """Discover and register decorated methods via introspection.

    This method inspects all methods on the plugin instance and looks for
    methods decorated with @hook or @event. It stores
    their metadata for later registration during start().

    This is hot-loading safe because:
    1. Discovery happens at instance creation (__init__), not class definition
    2. Each instance gets its own bound methods
    3. Method identity is unique per instance

    Args:
        instance: PluginProtocol instance to inspect

    Returns:
        Tuple of (hooks, event_handlers)
        - hooks: Dict of {hook_name: [(method, priority), ...]}
        - event_handlers: Dict of {event_pattern: [{"method": ...}, ...]}
    """
    hooks: dict[str, list[tuple[Callable, int]]] = {}
    event_handlers: dict[str, list[dict[str, Any]]] = {}

    # Iterate through all methods of this instance.
    # Dunder methods (__foo__) are skipped — they are never plugin handlers.
    # Single-underscore private methods (_foo) are scanned normally; @event and
    # @hook work on private methods just as on public ones.
    for name, method in inspect.getmembers(instance, predicate=inspect.ismethod):
        if name.startswith("__"):
            continue

        # Check for @hook decorators
        if hasattr(method, _HOOK_MARKER):
            for hook_info in getattr(method, _HOOK_MARKER):
                # Use hook name as-is (no prefixing)
                hook_name = hook_info["name"]
                hooks.setdefault(hook_name, []).append((method, hook_info["priority"]))
                logger.debug(
                    f"Discovered hook: {hook_name} "
                    f"(priority={hook_info['priority']}) on method {name}"
                )

        # Check for @event decorators
        if hasattr(method, _ON_HANDLER_MARKER):
            for handler_info in getattr(method, _ON_HANDLER_MARKER):
                # Handle both old string format and new dict format for backward compatibility
                if isinstance(handler_info, dict):
                    event_pattern = handler_info["pattern"]
                else:
                    # Old format: just the pattern string
                    event_pattern = handler_info
                # A list per pattern: several methods may handle the same event.
                event_handlers.setdefault(event_pattern, []).append({"method": method})
                logger.debug("Discovered event handler: %s on method %s", event_pattern, name)

    # Type conversion for mypy compatibility
    typed_hooks: dict[str, list[tuple[Callable[..., Any], int]]] = {
        str(name): hook_list for name, hook_list in hooks.items()
    }

    # Convert event handler keys to strings
    typed_event_handlers: dict[str, list[dict[str, Any]]] = {
        str(pattern): infos for pattern, infos in event_handlers.items()
    }

    return typed_hooks, typed_event_handlers
