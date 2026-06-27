"""AlertFormat — a hook handler that prettifies alert messages.

This plugin exists purely to demonstrate the **hook system as an extension point**. It
registers a handler for ``format_alert`` (the hook ``Thresholds`` calls). When present, its
return value becomes the alert message; when absent, ``Thresholds`` falls back to a plain
default. Crucially, ``Thresholds`` was never modified to accommodate this plugin — that is
the whole point of hooks: behavior is extended from the outside.

A ``@hook`` handler can also declare a ``priority``; with several handlers the highest
priority runs first, and ``firstresult`` callers take the first non-``None`` result.
"""

from __future__ import annotations

from uxok import Plugin, hook


class AlertFormat(Plugin):
    """Formats ``format_alert`` payloads into a human-readable string."""

    def __init__(self) -> None:
        # The `format_alert` hook handler is registered by the @hook decorator below;
        # it surfaces automatically as this plugin's `hooks_provided` (a derived field).
        super().__init__(name="alert_format")

    @hook("format_alert")
    def _format(self, reading: dict) -> str:
        celsius = reading.get("celsius", "?")
        seq = reading.get("seq", "?")
        return f"🔥 sample #{seq}: {celsius}°C is too hot"
