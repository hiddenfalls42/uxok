"""Tests for emit(at_tick=N) scheduled event emission."""

from __future__ import annotations

import asyncio

import pytest

from tests.fake_time import FakeTime, install_fake_time, run_ticks
from uxok import Core
from uxok.plugin import Plugin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class SenderPlugin(Plugin):
    """Minimal plugin with an emit(at_tick=) convenience wrapper."""

    def __init__(self) -> None:
        super().__init__(name="sender_plugin")
        self.received: list[dict] = []

    async def on_start(self) -> None:
        pass

    async def send_at(self, tick: int) -> None:
        await self.emit("scheduled.event", {"value": 1}, at_tick=tick)


def fake_core_100() -> tuple[Core, FakeTime]:
    """100 Hz fake-time core."""
    core = Core(tick_rate=100, hook_precaching="disabled")
    fake = FakeTime()
    install_fake_time(core, fake)
    return core, fake


# ---------------------------------------------------------------------------
# Kept: past-tick validation
# ---------------------------------------------------------------------------


class TestEmitScheduling:
    """Scheduled emit: exact tick and boundary cases."""

    @pytest.mark.asyncio
    async def test_emit_at_past_tick_raises(self, clean_core: Core) -> None:
        """Scheduling in the past raises ValueError immediately."""
        core = clean_core
        plugin = SenderPlugin()
        plugin._attach_core(core)
        await core.start()

        current = core.tick
        with pytest.raises(ValueError, match="in the past"):
            await plugin.emit("test.event", {}, at_tick=current - 1)

    # -----------------------------------------------------------------------
    # Replaced: exact tick (was a sleep-race approximation)
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_emit_at_fires_at_exact_tick(self) -> None:
        """emit(at_tick=T+5) fires at exactly the 5th driven boundary.

        Race-free negative: after driving 4 boundaries received == [];
        after the 5th boundary, drain the scheduled + dispatch tasks, then
        exactly one event arrives with event.tick == T+5.
        """
        core, fake = fake_core_100()
        clock = core._tick_clock
        received: list = []
        signal = asyncio.Event()

        async def handler(event):
            received.append(event)
            signal.set()

        await core.events.subscribe("scheduled.event", handler)
        await core.start()

        plugin = SenderPlugin()
        # In the no-gate model, register_plugin completes directly.
        await core.register_plugin(plugin)

        # Warm up 2 ticks so we have a defined baseline.
        await run_ticks(fake, clock, 2)
        target_tick = clock.tick + 5  # T+5

        await plugin.send_at(target_tick)

        # Drive 4 boundaries: event must NOT fire yet (scheduled task fires at T+5).
        # Check BEFORE extra yields to avoid the clock pre-advancing via cooperative yields.
        for i in range(4):
            await run_ticks(fake, clock, 1)
            assert received == [], f"Event fired early at boundary {i + 1}"

        # Drive the 5th boundary: scheduled task launches, which publishes the event.
        await run_ticks(fake, clock, 1)

        # Drain the scheduled task and the resulting dispatch task.
        await asyncio.wait_for(signal.wait(), timeout=2.0)

        assert len(received) == 1
        assert received[0].tick == target_tick

        await core.stop()
