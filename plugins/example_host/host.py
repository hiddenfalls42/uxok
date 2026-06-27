"""host.py — composes the example plugin graph into a runnable program.

A *host* is the program that boots a :class:`~uxok.Core`, registers a graph of plugins on
it, and keeps it alive. This is the smallest honest example of that shape:

    build_host(core)  →  register the graph, return the ShutdownHandler
    main()            →  boot a Core, build the host, block until shutdown

The pipeline it builds:

    Sensor ──emit "reading"──▶ Thresholds ──hook "format_alert"──▶ AlertFormat
                                   │
                                   └──emit "alert"──▶ AlertLog

``build_host`` is shared by ``main`` and the test suite so both boot the identical stack —
the running program and the tested program never drift.

Run it:

    python -m plugins.example_host.host
"""

from __future__ import annotations

import asyncio
import logging
import sys

from plugins.example_host.alert_format import AlertFormat
from plugins.example_host.alert_log import AlertLog
from plugins.example_host.sensor import Sensor
from plugins.example_host.shutdown import ShutdownHandler
from plugins.example_host.thresholds import Thresholds
from uxok import Core

logger = logging.getLogger("example_host")


async def build_host(core: Core) -> ShutdownHandler:
    """Register the example plugin graph on ``core`` and return its ShutdownHandler.

    Registration order follows the dependency arrows: the formatter and the log subscriber
    come up before the threshold plugin that uses them, which comes up before the sensor
    that feeds it. The ShutdownHandler is registered last so it traps signals only once the
    graph is live.
    """
    await core.register_plugin(AlertFormat())
    await core.register_plugin(AlertLog())
    await core.register_plugin(Thresholds())
    await core.register_plugin(Sensor())
    shutdown = ShutdownHandler()
    await core.register_plugin(shutdown)
    return shutdown


async def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    core = Core(
        plugin_configs={
            # ~0.25s between samples at the default 1000 Hz tick rate, so the demo is lively.
            "sensor": {"interval_ticks": 250},
            "thresholds": {"hot_threshold": 30.0},
        },
    )

    async with core:
        shutdown = await build_host(core)

        # Let a few samples flow (long enough to cross the threshold at least once), then
        # read state back through the capability surface to show the graph is wired end to end.
        await asyncio.sleep(3.0)
        sensor = await core.get_capability("sensor")
        alert_log = await core.get_capability("alert_log")
        print(f"latest reading: {sensor.latest()}")  # noqa: T201 — demo output is the point
        print(f"alerts so far: {alert_log.recent()}")  # noqa: T201 — demo output is the point
        print("example host up — Ctrl-C to stop", file=sys.stderr)  # noqa: T201

        await shutdown.wait_for_shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
