"""Watcher — hot-reloads plugin files that change on disk.

Scans ``watch_dir`` on a self-rescheduling hook (``hook(name, at_tick=core.tick
+ n)``); a changed file hot-swaps the plugin of the same name via the
``kernel.lifecycle`` facet, a new file becomes a fresh plugin. ``watch_dir`` is
``REQUIRED``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from uxok import REQUIRED, ConfigField, CoreError, Plugin, PluginError

logger = logging.getLogger(__name__)

_NOT_PLUGINS = {"__init__.py", "host.py"}


class Watcher(Plugin):
    """Scans a folder every few ticks; hot-loads any plugin file that changed."""

    def __init__(self) -> None:
        super().__init__(
            name="watcher",
            requires={"kernel.lifecycle"},
            events_published={"watcher.reloaded", "watcher.load_failed"},
            config_schema={
                "watch_dir": ConfigField(str, REQUIRED, "folder whose *.py files are watched"),
                "interval_seconds": ConfigField(float, 0.25, "scan period, in seconds"),
            },
        )
        self._mtimes: dict[Path, float] = {}

    async def on_start(self) -> None:
        self._lifecycle = await self.get_capability("kernel.lifecycle")
        self._dir = Path(self.config("watch_dir"))
        self._mtimes = {path: path.stat().st_mtime for path in self._files()}
        # Seconds → ticks via the core's own clock config; at least one tick.
        self._interval = max(1, int(self.config("interval_seconds") * self.core.config.tick_rate))
        # Dynamic registration (what @hook desugars to), then arm the first scan.
        # A deferred hook is fire-and-forget: it schedules and returns None — no await.
        await self.register_hook("watcher.scan", self._scan)
        self.hook("watcher.scan", at_tick=self.core.tick + self._interval)

    def _files(self) -> list[Path]:
        return [p for p in sorted(self._dir.glob("*.py")) if p.name not in _NOT_PLUGINS]

    async def _scan(self) -> None:
        for path in self._files():
            mtime = path.stat().st_mtime
            if mtime == self._mtimes.get(path):
                continue
            self._mtimes[path] = mtime
            try:
                await self._lifecycle.load_plugin(path.read_text(), origin=str(path))
            except (PluginError, CoreError) as exc:
                logger.warning("watcher: load of %s failed: %s", path.name, exc)
                await self.emit("watcher.load_failed", {"origin": str(path), "error": str(exc)})
            else:
                await self.emit("watcher.reloaded", {"origin": str(path)})
        # Self-rescheduling: the handler re-arms itself for the next scan.
        self.hook("watcher.scan", at_tick=self.core.tick + self._interval)
