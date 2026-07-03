"""Pure formatting and log-payload helpers — no core/ dependency."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from uxok.utils._helpers import log_context

if TYPE_CHECKING:
    from uxok.protocols.events import Event


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


def build_plugin_error_event(
    plugin_id: str,
    plugin_name: str,
    source: str,
    error: BaseException | str | None,
    **extra: Any,
) -> Event:
    """Build a ``core.plugin_error`` Event with a consistent payload.

    All four emit sites use this builder to ensure the payload is identical
    in shape. ``plugin_name`` is ``""`` at the ``event_handler`` site where
    no plugin name is available; it is always present in the payload.

    Args:
        plugin_id: String representation of the plugin UUID.
        plugin_name: Plugin name; empty string when unknown.
        source: Origin label (``"lifecycle"``, ``"event_handler"``,
                ``"background_task"``).
        error: Exception or string representation of the error.
        **extra: Source-dependent keys (``phase``, ``event_name``,
                 ``task_name``, ``method``, …).
    """
    from uxok.protocols.events import Event  # local import avoids circular dep

    return Event(
        "core.plugin_error",
        {
            "plugin_id": plugin_id,
            "plugin_name": plugin_name,
            "source": source,
            "error": str(error),
            "error_type": type(error).__name__ if isinstance(error, BaseException) else "",
            **extra,
        },
    )
