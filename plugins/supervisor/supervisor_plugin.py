"""SupervisorPlugin — restart-on-failure policy built on kernel primitives.

This plugin is deliberately a pure consumer of the public kernel surface:
it subscribes to the core's failure signals (core.plugin_error,
core.hook_error), counts failures per watched plugin in a sliding window,
and restarts crashing plugins by unregistering them and registering a fresh
instance from a caller-supplied factory — carrying state across the restart
via the same get_state()/restore_state() contract hot reload uses.

Per the kernel architecture decision: failure *signals* live in core,
restart *policy* lives here. If this plugin could not be written cleanly,
the missing kernel primitive would be the finding.
"""

from __future__ import annotations

import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from uxok import Plugin, event

if TYPE_CHECKING:
    from collections.abc import Callable

    from uxok.protocols import Event as EventType
    from uxok.protocols import PluginProtocol


@dataclass
class _Watch:
    """Restart policy for one watched plugin."""

    factory: Callable[[], Any]
    max_failures: int = 3
    window_s: float = 60.0
    failures: list[float] = field(default_factory=list)
    gave_up: bool = False
    restarts: int = 0


class SupervisorPlugin(Plugin):
    """Watches plugins via core failure signals and restarts crashers.

    Usage:
        supervisor = SupervisorPlugin()
        await core.register_plugin(supervisor)
        supervisor.watch("motor_controller", factory=lambda: MotorController(),
                         max_failures=3, window_s=60.0)

    The factory must return a fresh, unstarted plugin instance with the same
    name. On restart, the failing instance's get_state() is captured
    (best-effort) and handed to the new instance's restore_state().

    Graph control is reached through the tier-2 ``kernel.lifecycle`` grant declared in
    ``requires`` (RFC 0001 §2d), so the supervisor works unchanged under every
    ``capability_access`` mode.
    """

    def __init__(self) -> None:
        super().__init__(
            name="supervisor",
            provides={"supervision"},
            requires={"kernel.lifecycle"},
            events_published={"supervisor.restarted", "supervisor.gave_up"},
        )
        self._watches: dict[str, _Watch] = {}
        self._restarting: set[str] = set()

    # ========== Public API ==========

    def watch(
        self,
        plugin_name: str,
        factory: Callable[[], Any],
        *,
        max_failures: int = 3,
        window_s: float = 60.0,
    ) -> None:
        """Watch a plugin: restart it on failure, give up past the budget.

        Args:
            plugin_name: Name of the plugin to supervise.
            factory: Zero-argument callable returning a fresh plugin instance.
            max_failures: Failures tolerated within the window before giving up.
            window_s: Sliding window for failure counting, in seconds.
        """
        self._watches[plugin_name] = _Watch(
            factory=factory, max_failures=max_failures, window_s=window_s
        )

    def unwatch(self, plugin_name: str) -> None:
        """Stop supervising a plugin."""
        self._watches.pop(plugin_name, None)

    # ========== Failure signal handlers ==========

    @event("core.plugin_error")
    async def _on_plugin_error(self, ev: EventType) -> None:
        name = ev.data.get("plugin_name") or await self._name_from_id(ev.data.get("plugin_id", ""))
        if name:
            await self._record_failure(name)

    @event("core.hook_error")
    async def _on_hook_error(self, ev: EventType) -> None:
        name = await self._name_from_id(ev.data.get("plugin_id", ""))
        if name:
            await self._record_failure(name)

    # ========== Policy ==========

    async def _record_failure(self, plugin_name: str) -> None:
        watch = self._watches.get(plugin_name)
        if watch is None or watch.gave_up or plugin_name in self._restarting:
            return

        now = time.monotonic()
        watch.failures.append(now)
        watch.failures = [t for t in watch.failures if now - t <= watch.window_s]

        if len(watch.failures) > watch.max_failures:
            watch.gave_up = True
            await self.emit(
                "gave_up",
                {
                    "plugin_name": plugin_name,
                    "failures_in_window": len(watch.failures),
                    "restarts": watch.restarts,
                },
            )
            return

        await self._restart(plugin_name, watch)

    async def _restart(self, plugin_name: str, watch: _Watch) -> None:
        self._restarting.add(plugin_name)
        try:
            lc = await self.get_capability("kernel.lifecycle")
            state: dict = {}
            old = await lc.get_plugin(plugin_name)
            if old is not None:
                with suppress(Exception):
                    state = await old.get_state()
                with suppress(Exception):
                    await lc.unregister_plugin(plugin_name, force=True)

            new_instance = watch.factory()
            await lc.register_plugin(new_instance)
            with suppress(Exception):
                await new_instance.restore_state(state)

            watch.restarts += 1
            await self.emit(
                "restarted",
                {
                    "plugin_name": plugin_name,
                    "restarts": watch.restarts,
                    "state_carried": bool(state),
                },
            )
        finally:
            self._restarting.discard(plugin_name)

    # ========== Helpers ==========

    async def _name_from_id(self, plugin_id: str) -> str | None:
        if not plugin_id:
            return None
        lc = await self.get_capability("kernel.lifecycle")
        plugin: PluginProtocol | None = await lc.get_plugin(plugin_id)
        return plugin.metadata.name if plugin else None
