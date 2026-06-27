"""Sensor — a periodic reading source built on the tick system.

This is the head of the example pipeline. It shows three kernel primitives at once:

1. **The tick system + self-rescheduling recurrence.** uxok has no ``every_ticks``
   primitive on purpose (convention over configuration). Periodic work is built by a
   handler re-arming itself: ``emit("sensor.sample", at_tick=core.tick + interval)``.
   ``on_start`` fires the first sample; each sample schedules the next.
2. **Event publishing.** Each sample emits a ``reading`` event onto the bus. The sensor
   does not know — or care — who consumes it. ``Thresholds`` happens to subscribe.
3. **Being a capability provider.** It ``provides={"sensor"}``; any plugin (or the host's
   ``main``) can ``await core.get_capability("sensor")`` and call :meth:`latest`. That is
   the synchronous request/response side of the kernel, complementing the async event bus.

Readings are a fixed, cycled sequence rather than random noise: the framework values
predictable behavior (same input → same output), and it keeps the example's tests
deterministic without mocking the clock.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from uxok import ConfigField, Plugin, event

if TYPE_CHECKING:
    from uxok.protocols import Event as EventType

# A deterministic temperature cycle (°C). It deliberately crosses the default hot
# threshold (30.0) so the alert path always exercises end to end.
_READINGS = (19.0, 21.0, 26.0, 31.0, 28.0, 23.0)


class Sensor(Plugin):
    """Emits a ``reading`` event every ``interval_ticks`` ticks and remembers the last one.

    Config:
        interval_ticks: Ticks between samples (default 1000 ≈ 1s at the 1000 Hz default
            tick rate).
    """

    def __init__(self) -> None:
        super().__init__(
            name="sensor",
            provides={"sensor"},
            events_published={"reading", "sensor.sample"},
            config_schema={
                "interval_ticks": ConfigField(
                    int, 1000, "Ticks between sensor samples (1000 ≈ 1s at 1000 Hz)"
                ),
            },
        )
        self._index = 0
        self._latest: dict | None = None

    # ========== Public capability surface ==========

    def latest(self) -> dict | None:
        """Return the most recent reading, or ``None`` before the first sample."""
        return self._latest

    # ========== Lifecycle ==========

    async def on_start(self) -> None:
        """Arm the first sample. Each sample re-arms the next (see :meth:`_sample`)."""
        await self._arm_next()

    # ========== Recurrence ==========

    @event("sensor.sample")
    async def _sample(self, _ev: EventType) -> None:
        """Produce one reading, publish it, then schedule the next sample."""
        celsius = _READINGS[self._index % len(_READINGS)]
        self._index += 1
        self._latest = {"celsius": celsius, "seq": self._index}
        await self.emit("reading", self._latest)
        await self._arm_next()

    async def _arm_next(self) -> None:
        """Schedule the next sample one interval ahead on the tick clock.

        ``at_tick`` deferral is the kernel's only timing primitive; a positive offset
        from ``core.tick`` guarantees the required ``at_tick > core.tick``.
        """
        interval = max(1, self.config("interval_ticks"))
        await self.emit("sensor.sample", at_tick=self.core.tick + interval)
