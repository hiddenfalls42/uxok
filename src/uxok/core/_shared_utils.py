"""Core-internal shared utilities for lifecycle, logging, and validation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from uxok.protocols.events import Event
from uxok.utils import (
    log_context,
    safe_str,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from uxok.protocols._types import PluginId


async def drain_plugin_resources(
    plugin_id: PluginId,
    plugin_obj: Any,
    event_bus: Any,
    hook_executor: Any,
    capability_system: Any,
    logger: logging.Logger,
    scheduler: Any = None,
    *,
    emit_revocation: bool = True,
) -> None:
    """Unified drain of plugin resources in proper order.

    Scheduler entries are drained by instance identity (the plugin object),
    so hot reload can drain the old instance without touching schedules the
    new instance registered under the same plugin ID.

    When ``emit_revocation`` is True (genuine unregistration), a
    ``core.capability.revoked`` event is published for each capability whose
    last provider was this plugin. The failed-register rollback path passes
    ``False``: a plugin that never fully registered should not announce
    revocation of capabilities it briefly held.
    """
    pid_str = str(plugin_id)
    logger.debug("Draining resources", extra={"plugin_id": pid_str})

    async def _safe(coro: Callable[[], Awaitable[None]], success_msg: str, error_msg: str) -> None:
        try:
            await coro()
            logger.debug(success_msg, extra={"plugin_id": pid_str})
        except Exception as e:
            logger.warning(error_msg, extra={"plugin_id": pid_str, "error": safe_str(e)})

    await _safe(
        lambda: event_bus.unsubscribe_plugin(plugin_id),
        "Unsubscribed events",
        "Error unsubscribing events",
    )
    await _safe(
        lambda: hook_executor.unregister_plugin_hooks(pid_str),
        "Unregistered hooks",
        "Error unregistering hooks",
    )
    if capability_system is not None:
        try:
            revoked = await capability_system.unregister_capabilities_by_plugin(pid_str)
            logger.debug("Unregistered capabilities", extra={"plugin_id": pid_str})
        except Exception as e:
            revoked = []
            logger.warning(
                "Error unregistering capabilities",
                extra={"plugin_id": pid_str, "error": safe_str(e)},
            )
        if emit_revocation and revoked:
            for cap_name in revoked:
                try:
                    await event_bus.publish(
                        Event(
                            "core.capability.revoked",
                            {"capability": cap_name, "old_provider_id": pid_str},
                        )
                    )
                except Exception as e:
                    logger.warning(
                        "Error publishing capability revocation",
                        extra={"plugin_id": pid_str, "error": safe_str(e)},
                    )

    if scheduler is not None and plugin_obj is not None:
        try:
            scheduler.unschedule_owner(plugin_obj)
            logger.debug("Unscheduled tick operations", extra={"plugin_id": pid_str})
        except Exception as e:
            logger.warning(
                "Error unscheduling tick operations",
                extra={"plugin_id": pid_str, "error": safe_str(e)},
            )

    if plugin_obj and hasattr(plugin_obj, "_task_manager"):
        await plugin_obj._task_manager.cancel_all()
        logger.debug("Cancelled background tasks", extra={"plugin_id": plugin_id})

    logger.debug("Resource drain completed", extra={"plugin_id": plugin_id})


async def resolve_plugin(plugin_id: Any, registry: Any) -> tuple[Any, Any]:
    """Resolve a plugin by ID (UUID) or name string.

    Args:
        plugin_id: Plugin UUID, UUID string, or name string
        registry: Registry with get() and all() methods

    Returns:
        Tuple of (resolved_plugin_or_None, resolved_plugin_id)
    """
    if isinstance(plugin_id, str):
        try:
            import uuid

            parsed = uuid.UUID(plugin_id)
            return await registry.get(parsed), parsed
        except ValueError:
            # Not a UUID — treat as plugin name
            for p in (await registry.all()).values():
                if p.metadata.name == plugin_id:
                    return p, p.metadata.id
            return None, plugin_id
    return await registry.get(plugin_id), plugin_id


def log_op(operation: str, **kwargs: Any) -> dict[str, Any]:
    """Standard structured log payload for any operation."""
    return log_context(operation=operation, **kwargs)


def format_capability_error(capability: str | list[str], available: list[str] | None = None) -> str:
    """Consistent capability error formatting."""
    missing = ", ".join(sorted(capability)) if isinstance(capability, list) else capability
    if available:
        return f"Capability '{missing}' not available. Available: {', '.join(sorted(available))}"
    return f"Capability '{missing}' not available."


def format_plugin_error(
    plugin_id: str,
    reason: str,
    available_options: list[str] | None = None,
) -> str:
    """Consistent plugin error formatting."""
    base = f"Plugin {plugin_id}: {reason}"
    if available_options:
        return f"{base}. Options: {', '.join(sorted(available_options))}"
    return base
