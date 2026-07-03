"""Pure formatting and log-payload helpers — no core/ dependency."""

from __future__ import annotations

from typing import Any

from uxok.utils._helpers import log_context


def log_op(operation: str, **kwargs: Any) -> dict[str, Any]:
    """Standard structured log payload for any operation."""
    return log_context(operation=operation, **kwargs)


def format_capability_error(
    capability: str | list[str], available: list[str] | None = None
) -> str:
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
