"""Roster — mirrors the live plugin graph as it changes.

Pure observation, zero grants: ambient under every ``capability_access`` mode.
Registration traffic arrives via the ``plugin.registered``/``unregistered``
hooks, swap traffic via the ``core.plugin_reloaded`` and ``core.capability.*``
events. ``roster.report`` answers with a one-line summary from ``core.list()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from uxok import Plugin, event, hook

if TYPE_CHECKING:
    from uxok.protocols import Event as EventType


class Roster(Plugin):
    """Prints graph changes; answers the ``roster.report`` hook with a live summary."""

    def __init__(self) -> None:
        super().__init__(name="roster")
        self._names: dict[str, str] = {}  # plugin id -> name (ids outlive departures)

    async def on_start(self) -> None:
        # Snapshot whoever committed before us, then mirror every change after.
        for view in await self.core.list():
            self._names[view.id] = view.name
        await self.register_hook("plugin.registered", self._on_registered)
        await self.register_hook("plugin.unregistered", self._on_unregistered)

    async def _on_registered(self, plugin: Any) -> None:
        meta = plugin.metadata
        self._names[str(meta.id)] = meta.name
        provides = f" (provides {', '.join(sorted(meta.provides))})" if meta.provides else ""
        print(f"roster: + {meta.name}{provides}")  # noqa: T201 — demo output is the point

    async def _on_unregistered(self, plugin_id: Any) -> None:
        name = self._names.pop(str(plugin_id), str(plugin_id))
        print(f"roster: - {name}")  # noqa: T201

    @event("core.plugin_reloaded")
    async def _on_reloaded(self, ev: EventType) -> None:
        print(f"roster: ~ {ev.data['plugin_name']} hot-swapped")  # noqa: T201

    @event("core.capability.*")
    async def _on_capability_change(self, ev: EventType) -> None:
        change = ev.name.rsplit(".", 1)[-1]  # "rebound" | "revoked"
        print(f"roster: ~ capability {ev.data['capability']} {change}")  # noqa: T201

    @hook("roster.report")
    async def report(self) -> str:
        plugins = await self.core.list()
        capabilities = ", ".join(plugins.capabilities)
        return f"{plugins.count} plugins live; capabilities: {capabilities}"
