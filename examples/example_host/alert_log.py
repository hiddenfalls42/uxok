"""AlertLog — records alerts, and shows state continuity across hot reloads.

The tail of the pipeline. Two lessons:

- **Event-bus decoupling.** It subscribes to ``alert`` and keeps a history. It is wholly
  independent of ``Thresholds`` — you could add a metrics plugin, a pager plugin, or a
  second log, all subscribing to the same event, without touching the emitter.
- **State continuity.** Plugin instances are one-shot; hot reload swaps in a fresh instance.
  A plugin that wants its state to survive that swap implements the
  ``get_state``/``restore_state`` contract. Here the alert history is carried across.

It also ``provides={"alert_log"}`` so the history is queryable on demand via
``get_capability("alert_log").recent()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from uxok import Plugin, event

if TYPE_CHECKING:
    from uxok.protocols import Event as EventType


class AlertLog(Plugin):
    """Subscribes to ``alert`` events and retains a bounded history."""

    def __init__(self, max_history: int = 100) -> None:
        super().__init__(name="alert_log", provides={"alert_log"})
        self._max_history = max_history
        self._alerts: list[dict] = []

    # ========== Public capability surface ==========

    def recent(self, limit: int | None = None) -> list[dict]:
        """Return recorded alerts, newest last; capped at ``limit`` if given."""
        return self._alerts[-limit:] if limit else list(self._alerts)

    # ========== Event subscriber ==========

    @event("alert")
    async def _on_alert(self, ev: EventType) -> None:
        self._alerts.append(ev.data or {})
        # Bound the history so a long-running host never grows without limit.
        if len(self._alerts) > self._max_history:
            self._alerts = self._alerts[-self._max_history :]

    # ========== State continuity across hot reload ==========

    async def get_state(self) -> dict:
        """Capture the alert history so a reloaded instance can restore it."""
        return {"alerts": list(self._alerts)}

    async def restore_state(self, state: dict) -> None:
        """Restore history captured by a prior instance's :meth:`get_state`."""
        self._alerts = list(state.get("alerts", []))
