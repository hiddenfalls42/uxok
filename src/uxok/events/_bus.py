"""Event bus implementation — concurrent fire-and-forget dispatch."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from uxok.events._subscriptions import SubscriptionManager
from uxok.protocols import CoreConfig, Event
from uxok.utils import build_plugin_error_event

if TYPE_CHECKING:
    from collections.abc import Callable

    from uxok.protocols._types import PluginId

logger = logging.getLogger(__name__)


class _EventBus:
    """Event bus with concurrent fire-and-forget dispatch.

    Each subscriber callback is launched as its own tracked asyncio task so
    that a slow subscriber never blocks the publisher or other subscribers.
    Tasks are tracked in ``_dispatch_tasks``; finished tasks remove themselves
    via a done-callback.  Call ``drain()`` on shutdown to cancel and settle any
    in-flight tasks.

    During bootstrap (no running event loop), callbacks execute inline to
    preserve safety before the loop is available.
    """

    def __init__(
        self,
        config: CoreConfig | None = None,
        clock: Any | None = None,
    ) -> None:
        self._config = config or CoreConfig()
        self._clock = clock

        self._subscriptions = SubscriptionManager()
        self._dispatch_tasks: set[asyncio.Task[None]] = set()

    async def publish(self, event: Event) -> bool:
        """Publish an event to all subscribers as concurrent tracked tasks."""
        if self._subscriptions.is_muted(event.name):
            return True

        subscribers = self._subscriptions.get_subscribers(event.name)
        if not subscribers:
            return True

        if self._clock is not None and self._clock.tick > 0:
            event = Event(
                name=event.name,
                data=event.data,
                timestamp=event.timestamp,
                tick=self._clock.tick,
                slip=self._clock.slip,
                source=event.source,
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        for callback, is_async, plugin_id in subscribers:
            coro = self._safe_callback(callback, is_async, event, plugin_id)
            if loop is not None:
                task = loop.create_task(coro)
                self._dispatch_tasks.add(task)
                task.add_done_callback(self._dispatch_tasks.discard)
            else:
                # Bootstrap path: no event loop yet — execute inline.
                await coro

        return True

    async def drain(self) -> None:
        """Cancel all in-flight dispatch tasks and await their settling.

        Called from ``Core.stop()`` (after the clock is stopped) so that no
        orphaned subscriber callbacks outlive the core. Loops until the set is
        empty: a callback that errors mid-drain may publish ``core.plugin_error``,
        scheduling new dispatch tasks after the initial snapshot.
        """
        while self._dispatch_tasks:
            tasks = list(self._dispatch_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._dispatch_tasks.difference_update(tasks)

    async def _safe_callback(
        self,
        callback: Callable[[Event], Any],
        is_async: bool,
        event: Event,
        plugin_id: PluginId | None = None,
    ) -> None:
        """Execute callback with error isolation.

        Failures publish a core.plugin_error event (source: event_handler) so
        supervision policy can observe them — except failures while handling
        core.plugin_error itself, which only log (no error loops).
        """
        try:
            if is_async:
                await callback(event)
            else:
                callback(event)
        except Exception as e:
            logger.exception(
                "Error in event subscriber for event '%s'",
                event.name,
                extra={"event_name": event.name, "event_data": event.data},
            )
            if event.name == "core.plugin_error":
                return
            try:
                await self.publish(
                    build_plugin_error_event(
                        str(plugin_id) if plugin_id else "",
                        "",
                        "event_handler",
                        e,
                        event_name=event.name,
                    )
                )
            except Exception:
                logger.debug("Failed to publish core.plugin_error", exc_info=True)

    async def subscribe(
        self,
        event_name: str,
        callback: Callable[[Event], None],
        plugin_id: PluginId | None = None,
        owner: object | None = None,
    ) -> str:
        """Subscribe to an event.

        Returns:
            Subscription ID for unsubscribing
        """
        return self._subscriptions.subscribe(event_name, callback, plugin_id, owner=owner)

    async def unsubscribe_owner(self, owner: object) -> None:
        """Remove all subscriptions belonging to a specific plugin instance."""
        self._subscriptions.unsubscribe_owner(owner)

    async def unsubscribe(
        self,
        subscription_id: str,
    ) -> None:
        """Unsubscribe from an event using subscription ID."""
        self._subscriptions.unsubscribe(subscription_id)

    async def unsubscribe_plugin(self, plugin_id: PluginId) -> None:
        """Unsubscribe all callbacks for a specific plugin.

        Args:
            plugin_id: UUID of the plugin to unsubscribe all callbacks for.
        """
        self._subscriptions.unsubscribe_plugin(plugin_id)

    def has_subscribers(self, name: str) -> bool:
        """Return True if name has at least one subscriber and is not muted.

        Mute-aware: returns False when the event is suppressed, so a
        demand-driven emitter can cheaply skip work when nobody is listening.
        """
        return self._subscriptions.has_subscribers(name)

    def mute(self, pattern: str) -> None:
        """Suppress all events whose name matches pattern (fnmatch syntax).

        Muted events are dropped in publish() before subscriber lookup —
        no dispatch tasks are created. This is a host/mechanism primitive
        for demand-driven emission; plugin authors use @event / emit().
        """
        self._subscriptions.mute(pattern)

    def unmute(self, pattern: str) -> None:
        """Remove a previously added mute pattern.

        Silently does nothing if the pattern was not muted.
        """
        self._subscriptions.unmute(pattern)
