"""Example host: the sensor/alerting reference graph built purely on kernel primitives.

This suite is also the acceptance test for the example: every kernel primitive the host
demonstrates is exercised here — the event bus (reading → alert), the hook extension point
(format_alert), the capability surface (get_capability), state continuity
(get_state/restore_state), and graceful shutdown (system.shutdown). The whole suite runs
under both ``capability_access="open"`` and ``"declared"`` so the graph is proven under the
enforced secure-capability mode, not just the permissive one.

The pipeline is driven by publishing ``reading`` events directly rather than waiting on the
tick clock, so assertions are deterministic. The Sensor's own tick-driven sampling is
covered in isolation with an interval large enough that the clock never fires it mid-test.
"""

import asyncio

import pytest
import pytest_asyncio
from examples.example_host import AlertFormat, AlertLog, Sensor, ShutdownHandler, Thresholds
from examples.example_host.host import build_host

from uxok import Core
from uxok.protocols import Event


@pytest_asyncio.fixture(params=["open", "declared"])
async def core(request):
    """A fresh core under each capability_access mode, with guaranteed cleanup."""
    from uxok.protocols import CoreState

    c = Core(capability_access=request.param)
    try:
        yield c
    finally:
        if c.state is CoreState.RUNNING:
            await c.stop()


async def _drain(seconds: float = 0.15):
    """Let fire-and-forget event dispatch (and its nested emits) settle."""
    await asyncio.sleep(seconds)


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_host_registers_graph_and_exposes_capabilities(core):
    shutdown = await build_host(core)

    assert isinstance(shutdown, ShutdownHandler)
    # The provider plugins are resolvable through the capability surface.
    sensor = await core.get_capability("sensor")
    alert_log = await core.get_capability("alert_log")
    assert sensor is not None
    assert alert_log.recent() == []


# ---------------------------------------------------------------------------
# Event bus + hook extension point
# ---------------------------------------------------------------------------


async def _pipeline(core, *, with_formatter: bool) -> AlertLog:
    """Register the reading→alert pipeline (optionally with the formatter) and return the log."""
    if with_formatter:
        await core.register_plugin(AlertFormat())
    log = AlertLog()
    await core.register_plugin(log)
    await core.register_plugin(Thresholds())
    return log


@pytest.mark.asyncio
async def test_hot_reading_produces_formatted_alert(core):
    log = await _pipeline(core, with_formatter=True)

    # A cold reading is below threshold: no alert.
    await core.events.publish(Event("reading", {"celsius": 19.0, "seq": 1}))
    await _drain()
    assert log.recent() == []

    # A hot reading crosses the threshold: one alert, formatted by the hook handler.
    await core.events.publish(Event("reading", {"celsius": 31.0, "seq": 2}))
    await _drain()
    alerts = log.recent()
    assert len(alerts) == 1
    assert "🔥" in alerts[0]["message"]
    assert alerts[0]["celsius"] == 31.0


@pytest.mark.asyncio
async def test_alert_fires_without_a_formatter(core):
    """The format_alert hook is a genuine opt-in: the alert path works with no handler."""
    log = await _pipeline(core, with_formatter=False)

    await core.events.publish(Event("reading", {"celsius": 31.0, "seq": 1}))
    await _drain()

    alerts = log.recent()
    assert len(alerts) == 1
    # Default message (no formatter installed), not the AlertFormat string.
    assert "🔥" not in alerts[0]["message"]
    assert "at or above" in alerts[0]["message"]


@pytest.mark.asyncio
async def test_threshold_is_configurable(core):
    """A lower configured threshold makes an otherwise-cold reading alert."""
    core2 = Core(plugin_configs={"thresholds": {"hot_threshold": 20.0}})
    try:
        log = AlertLog()
        await core2.register_plugin(log)
        await core2.register_plugin(Thresholds())

        await core2.events.publish(Event("reading", {"celsius": 21.0, "seq": 1}))
        await _drain()
        assert len(log.recent()) == 1
    finally:
        from uxok.protocols import CoreState

        if core2.state is CoreState.RUNNING:
            await core2.stop()


# ---------------------------------------------------------------------------
# State continuity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alert_log_state_roundtrips(core):
    log = await _pipeline(core, with_formatter=True)
    await core.events.publish(Event("reading", {"celsius": 31.0, "seq": 1}))
    await _drain()

    state = await log.get_state()
    assert len(state["alerts"]) == 1

    # A fresh instance (as a hot reload would create) restores the prior history.
    replacement = AlertLog()
    await replacement.restore_state(state)
    assert replacement.recent() == log.recent()


# ---------------------------------------------------------------------------
# Sensor: tick-driven sampling, exercised deterministically
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sensor_sampling_produces_readings(core):
    readings = []

    async def on_reading(ev):
        readings.append(ev.data)

    await core.events.subscribe("reading", on_reading)
    # The default interval (~1s at 1000 Hz) far exceeds this test's runtime, so the clock
    # never auto-fires the sensor. We step it by publishing sensor.sample ourselves, which
    # makes the sampled sequence deterministic.
    sensor = Sensor()
    await core.register_plugin(sensor)

    # Each sensor.sample drives exactly one reading along the fixed cycle.
    for _ in range(3):
        await core.events.publish(Event("sensor.sample", {}))
        await _drain(0.05)

    assert [r["celsius"] for r in readings[:3]] == [19.0, 21.0, 26.0]
    assert sensor.latest() == {"celsius": 26.0, "seq": 3}


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_event_unblocks_wait(core):
    handler = ShutdownHandler()
    await core.register_plugin(handler)

    waiter = asyncio.create_task(handler.wait_for_shutdown())
    await asyncio.sleep(0)
    assert not waiter.done()

    # Any plugin emitting system.shutdown unblocks the host loop.
    await core.events.publish(Event("system.shutdown", {"source": "test"}))
    await asyncio.wait_for(waiter, timeout=1.0)
    assert waiter.done()
