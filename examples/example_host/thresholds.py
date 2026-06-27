"""Thresholds — turns hot readings into alerts, with an extension hook for the message.

This is the middle of the pipeline and the example's clearest lesson in *decoupling*:

- It **subscribes** to ``reading`` (``@event``). It never references ``Sensor``; it only
  knows the event contract. Any reading source would feed it.
- When a reading is at or above ``hot_threshold`` it builds the alert message through the
  ``format_alert`` **hook** (``firstresult=True``) — an extension point. If some plugin
  (here, ``AlertFormat``) has registered a handler, its message wins; if none has, the
  hook returns ``None`` and we fall back to a plain default. The alert path works with or
  without the formatter — that is what makes the hook a genuine opt-in extension rather
  than a hard dependency.
- It then **emits** an ``alert`` event. Again it does not know who listens (``AlertLog``
  does).

The threshold itself is a declared config field, so deployments tune policy without
touching code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from uxok import ConfigField, Plugin, event

if TYPE_CHECKING:
    from uxok.protocols import Event as EventType


class Thresholds(Plugin):
    """Emits an ``alert`` when a ``reading`` meets or exceeds ``hot_threshold``.

    Config:
        hot_threshold: Celsius value at or above which a reading raises an alert.
    """

    def __init__(self) -> None:
        super().__init__(
            name="thresholds",
            # No `requires`: readings arrive over the event bus, not as a capability.
            events_published={"alert"},
            hooks_consumed={"format_alert"},
            config_schema={
                "hot_threshold": ConfigField(
                    float, 30.0, "Celsius at or above which a reading raises an alert"
                ),
            },
        )

    @event("reading")
    async def _on_reading(self, ev: EventType) -> None:
        reading = ev.data or {}
        celsius = reading.get("celsius")
        if celsius is None or celsius < self.config("hot_threshold"):
            return

        # Ask the extension point for a message. firstresult returns the first non-None
        # handler result, or None if nobody is registered — hence the fallback.
        message = await self.hook("format_alert", reading, firstresult=True)
        if message is None:
            message = f"reading {celsius}°C at or above {self.config('hot_threshold')}°C"

        await self.emit(
            "alert", {"message": message, "celsius": celsius, "seq": reading.get("seq")}
        )
