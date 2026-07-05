"""Supervisor — consumes the kernel's error signals and evicts repeat offenders.

Counts ``core.plugin_error``/``core.hook_error`` per plugin; on the first
failure, defers a review with ``emit(at_tick=...)`` so a burst gets judged once,
after it settles. A plugin over ``max_errors`` by review time is evicted
through the ``kernel.lifecycle`` facet.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from uxok import ConfigField, Plugin, event

if TYPE_CHECKING:
    from uxok.protocols import Event as EventType

logger = logging.getLogger(__name__)


class Supervisor(Plugin):
    """Counts ``core.plugin_error`` per plugin; evicts repeat offenders after review."""

    def __init__(self) -> None:
        super().__init__(
            name="supervisor",
            requires={"kernel.lifecycle"},
            events_published={"supervisor.review", "supervisor.evicted"},
            config_schema={
                "max_errors": ConfigField(int, 3, "errors per plugin before eviction"),
                "review_delay_ticks": ConfigField(int, 50, "ticks to wait before reviewing"),
            },
        )
        self._errors: dict[str, int] = {}
        self._under_review: set[str] = set()

    async def on_start(self) -> None:
        self._lifecycle = await self.get_capability("kernel.lifecycle")

    @event("core.plugin_error")
    async def _on_plugin_error(self, ev: EventType) -> None:
        plugin_id = ev.data["plugin_id"]
        self._errors[plugin_id] = self._errors.get(plugin_id, 0) + 1
        logger.warning(
            "supervisor: %s failed in %s (%s): %s",
            ev.data.get("plugin_name", plugin_id),
            ev.data["source"],
            ev.data["error_type"],
            ev.data["error"],
        )
        if plugin_id not in self._under_review:
            self._under_review.add(plugin_id)
            # Deferred, debounced judgement: one review per error burst.
            review_at = self.core.tick + self.config("review_delay_ticks")
            await self.emit("supervisor.review", {"plugin_id": plugin_id}, at_tick=review_at)

    @event("core.hook_error")
    async def _on_hook_error(self, ev: EventType) -> None:
        # Hook failures are isolated to None by the kernel; surface them.
        logger.warning(
            "supervisor: hook %r failed in %s: %s",
            ev.data["hook_name"],
            ev.data["plugin_id"],
            ev.data["error"],
        )

    @event("supervisor.review")
    async def _review(self, ev: EventType) -> None:
        plugin_id = ev.data["plugin_id"]
        self._under_review.discard(plugin_id)
        count = self._errors.get(plugin_id, 0)
        if count < self.config("max_errors"):
            return
        offender = await self._lifecycle.get_plugin(plugin_id)
        if offender is None:
            return  # already gone
        name = offender.metadata.name
        self._errors.pop(plugin_id, None)
        print(f"supervisor: evicting {name} after {count} errors")  # noqa: T201
        await self._lifecycle.unregister_plugin(plugin_id, force=True)
        await self.emit("supervisor.evicted", {"plugin_id": plugin_id, "plugin_name": name})
