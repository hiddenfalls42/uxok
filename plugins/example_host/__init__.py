"""Example host: a tiny sensor/alerting program built on the uxok kernel.

A worked "hello world" that wires every kernel primitive — event bus, hook extension
points, capability provider/consumer, plugin lifecycle, the tick system, config schema,
state continuity, and graceful shutdown — into one runnable host. See ``host.py`` for the
composition and ``python -m plugins.example_host.host`` to run it.
"""

# Only the plugin classes are re-exported here. ``build_host``/``main`` live in
# ``plugins.example_host.host`` and are imported from there directly — importing the
# ``host`` submodule into this package __init__ would trip a runpy warning under
# ``python -m plugins.example_host.host``.
from plugins.example_host.alert_format import AlertFormat
from plugins.example_host.alert_log import AlertLog
from plugins.example_host.sensor import Sensor
from plugins.example_host.shutdown import ShutdownHandler
from plugins.example_host.thresholds import Thresholds

__all__ = [
    "AlertFormat",
    "AlertLog",
    "Sensor",
    "ShutdownHandler",
    "Thresholds",
]
