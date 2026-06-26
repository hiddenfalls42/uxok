"""Event system protocol definitions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from uxok.protocols._types import EventName, PluginId


@dataclass(frozen=True, slots=True)
class Event:
    """Event data structure - immutable.

    Events can carry any type of payload for maximum flexibility.

    Tick and slip fields are stamped by the core at publish time when the
    tick system is running. Values of 0 indicate the tick system is not
    yet active or has not been started.
    """

    name: EventName
    data: Any
    timestamp: float = 0.0
    tick: int = 0  # Stamped by core at publish time; 0 before tick system starts
    slip: int = 0  # Tick boundary drift at stamp time; 0 means on-schedule
    source: str | None = None  # Plugin name stamped by Plugin.emit(); None if published directly

    def __init__(
        self,
        name: EventName,
        data: Any,
        timestamp: float | None = None,
        tick: int = 0,
        slip: int = 0,
        source: str | None = None,
    ) -> None:
        """Initialize with optional timestamp and tick fields for testing.

        Args:
            name: Event name
            data: Event payload (can be any type)
            timestamp: Event timestamp (auto-generated if not provided)
            tick: Tick counter when event was published (0 if tick system not running)
            slip: Tick boundary drift in tick periods (0 means on-schedule)
            source: Emitting plugin's name (metadata only; not part of the topic name).
                    Stamped automatically by Plugin.emit(); None when published directly
                    via core.events.publish().
        """
        import time

        if timestamp is None:
            timestamp = time.time()

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "tick", tick)
        object.__setattr__(self, "slip", slip)
        object.__setattr__(self, "source", source)


@runtime_checkable
class EventBus(Protocol):
    """Core event system protocol."""

    async def publish(self, event: Event) -> bool:
        """Publish an event to all subscribers.

        Public API for host applications and framework code alike. Plugins
        usually prefer Plugin.emit(), which publishes the name verbatim and
        stamps Event.source with the emitting plugin's name.

        Args:
            event: The event to publish

        Returns:
            True if event was published successfully, False otherwise

        Note:
            Dispatch is concurrent: each subscriber callback runs as its own
            tracked asyncio task, so a slow subscriber never blocks others.
        """
        ...

    async def subscribe(
        self,
        event_name: EventName,
        callback: Callable[[Event], None],
        plugin_id: PluginId | None = None,
        owner: object | None = None,
    ) -> str:
        """Subscribe to an event.

        Public API for host applications. Plugins usually prefer the @event
        decorator, which registers handlers with lifecycle cleanup for free.

        Args:
            event_name: The name of the event to subscribe to
            callback: The callback to call when the event is published
            plugin_id: Optional plugin ID for automatic cleanup
            owner: Owning instance for instance-scoped cleanup (hot reload);
                   derived from bound methods automatically when omitted

        Returns:
            Subscription ID for unsubscribing
        """
        ...

    async def unsubscribe_owner(self, owner: object) -> None:
        """Remove all subscriptions belonging to a specific plugin instance.

        Args:
            owner: The plugin instance whose subscriptions should be removed
        """
        ...

    async def unsubscribe(self, subscription_id: str) -> None:
        """Unsubscribe from an event using subscription ID.

        Args:
            subscription_id: The subscription ID to remove
        """
        ...

    async def unsubscribe_plugin(self, plugin_id: PluginId) -> None:
        """Remove all subscriptions for a plugin.

        Args:
            plugin_id: The plugin ID whose subscriptions should be removed
        """
        ...

    def has_subscribers(self, name: str) -> bool:
        """Return True if name has at least one subscriber and is not muted.

        Lets host/mechanism code check whether an event would be delivered
        before generating it. Mute-aware: returns False when the topic is
        suppressed, so demand-driven emitters naturally skip muted topics.

        This is a host/mechanism primitive. Plugin authors use @event / emit().
        """
        ...

    def mute(self, pattern: str) -> None:
        """Suppress all events whose name matches pattern (fnmatch syntax).

        Muted events are dropped in publish() before subscriber lookup.
        This is a host/mechanism primitive. Plugin authors use @event / emit().
        """
        ...

    def unmute(self, pattern: str) -> None:
        """Remove a previously added mute pattern.

        Silently does nothing if the pattern was not muted.
        This is a host/mechanism primitive. Plugin authors use @event / emit().
        """
        ...
