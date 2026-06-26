"""Subscription management for event bus."""

from __future__ import annotations

import asyncio
import fnmatch
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from uxok.protocols import Event
    from uxok.protocols._types import EventName, PluginId

    # Type alias for subscription record:
    # (event_name, callback, is_async, plugin_id, owner)
    _SubscriptionRecord = tuple[
        EventName, Callable[[Event], None], bool, PluginId | None, object | None
    ]

# Module level singleton to avoid allocating empty tuple on every miss
_EMPTY_SUBSCRIBERS: tuple = ()


class SubscriptionManager:
    """Manages event subscriptions and pattern matching."""

    def __init__(self) -> None:
        # Use dict for pattern-based subscriber storage
        # Structure: {pattern: {plugin_id: [(callback, is_async)]}}
        self._subscribers: dict[
            EventName, dict[PluginId | None, list[tuple[Callable[[Event], None], bool]]]
        ] = {}

        # Track subscriptions by ID for unsubscribe by ID
        self._subscriptions_by_id: dict[str, _SubscriptionRecord] = {}

        # Optimization: track if any patterns contain wildcards
        self._has_wildcard_patterns = False

        # Cache for frequent exact matches (simple bounded dict)
        self._exact_match_cache: dict[
            EventName, tuple[tuple[Callable, bool, PluginId | None], ...]
        ] = {}
        self._max_cache_size = 100

        # Mute patterns: events matching any pattern are silenced at the bus level
        self._mute_patterns: set[str] = set()

    def subscribe(
        self,
        event_name: EventName,
        callback: Callable[[Event], None],
        plugin_id: PluginId | None = None,
        owner: object | None = None,
    ) -> str:
        """Subscribe to an event.

        Args:
            event_name: The name of the event to subscribe to
            callback: The callback to call when event is published
            plugin_id: Optional plugin ID for tracking subscriptions
            owner: Owning instance for instance-scoped cleanup (hot reload).
                   Derived from bound methods automatically when omitted.

        Returns:
            Subscription ID for unsubscribing
        """
        # Generate unique subscription ID
        subscription_id = str(uuid.uuid4())

        # Detect async once at subscribe time - O(1) bitmask check
        is_async = asyncio.iscoroutinefunction(callback)

        # Instance ownership: during hot reload old and new plugin instances
        # share a plugin ID, so per-instance cleanup needs the owner too.
        if owner is None:
            owner = getattr(callback, "__self__", None)

        # Store subscription by ID
        self._subscriptions_by_id[subscription_id] = (
            event_name,
            callback,
            is_async,
            plugin_id,
            owner,
        )

        # Check if pattern contains wildcards (for optimization)
        if not self._has_wildcard_patterns and ("*" in event_name or "?" in event_name):
            self._has_wildcard_patterns = True

        # Add to pattern-based storage - store (callback, is_async) pair
        if event_name not in self._subscribers:
            self._subscribers[event_name] = {}

        # Use plugin_id as key (None is valid)
        key = plugin_id
        if key not in self._subscribers[event_name]:
            self._subscribers[event_name][key] = []
        self._subscribers[event_name][key].append((callback, is_async))

        # Invalidate cache for this pattern
        self._invalidate_cache(event_name)

        return subscription_id

    def unsubscribe(
        self,
        subscription_id: str,
    ) -> None:
        """Unsubscribe using subscription ID.

        Args:
            subscription_id: The subscription ID to remove
        """
        if subscription_id not in self._subscriptions_by_id:
            return

        event_name, callback, _is_async, plugin_id, _owner = self._subscriptions_by_id.pop(
            subscription_id
        )

        # Track if removed pattern had wildcards
        had_wildcard = "*" in event_name or "?" in event_name

        if event_name in self._subscribers:
            group = self._subscribers[event_name]
            if plugin_id in group:
                # Remove ONE matching (callback, is_async) pair — duplicates
                # are separate subscriptions with their own IDs.
                entries = group[plugin_id]
                for i, (cb, _ia) in enumerate(entries):
                    if cb is callback:
                        del entries[i]
                        break
                if not entries:
                    del group[plugin_id]

            # Remove pattern if no plugin_ids remain
            if not self._subscribers[event_name]:
                del self._subscribers[event_name]

            # Invalidate cache for this event
            self._invalidate_cache(event_name)

            # Update wildcard flag if removed pattern had wildcards
            if had_wildcard:
                self._has_wildcard_patterns = any("*" in p or "?" in p for p in self._subscribers)

    def unsubscribe_owner(self, owner: object) -> int:
        """Remove all subscriptions belonging to a specific instance.

        Used by hot reload: the old and new plugin instances share a plugin
        ID, so the swap must drain by instance identity, not by ID.

        Returns:
            Number of subscriptions removed.
        """
        sids = [sid for sid, rec in self._subscriptions_by_id.items() if rec[4] is owner]
        for sid in sids:
            self.unsubscribe(sid)
        return len(sids)

    def unsubscribe_plugin(self, plugin_id: PluginId) -> None:
        """Unsubscribe all callbacks for a specific plugin.

        Delegates to unsubscribe() per subscription so there is exactly one
        removal path (cache invalidation and wildcard-flag maintenance live
        there, in the correct order — invalidate while the flag still reflects
        the removed pattern, then recompute the flag).

        plugin_id is canonically a ``PluginId`` (UUID) — the same value passed
        to subscribe(), which is what every plugin's ``metadata.id`` is. Passing
        a string form will simply match nothing (a safe no-op), matching the
        protocol's ``PluginId`` contract.

        Args:
            plugin_id: UUID of the plugin to unsubscribe.
        """
        sids = [sid for sid, rec in self._subscriptions_by_id.items() if rec[3] == plugin_id]
        for sid in sids:
            self.unsubscribe(sid)

    def get_subscribers(
        self, event_name: EventName
    ) -> tuple[tuple[Callable, bool, PluginId | None], ...]:
        """Get all subscribers matching event name.

        Args:
            event_name: Event name to match

        Returns:
            Tuple of (callback, is_async, plugin_id) triples that match the
            event. plugin_id attributes handler failures to their plugin.
        """
        # Cache hit - zero allocation, return stored tuple directly
        cached = self._exact_match_cache.get(event_name)
        if cached is not None:
            return cached

        # Build result as list
        result: list[tuple[Callable, bool, PluginId | None]] = []

        # Exact match path
        exact = self._subscribers.get(event_name)
        if exact:
            for plugin_id, plugin_group in exact.items():
                result.extend((cb, ia, plugin_id) for cb, ia in plugin_group)

            if self._has_wildcard_patterns:
                for pattern, plugin_groups in self._subscribers.items():
                    if pattern != event_name and fnmatch.fnmatch(event_name, pattern):
                        for plugin_id, pg in plugin_groups.items():
                            result.extend((cb, ia, plugin_id) for cb, ia in pg)

            out = tuple(result)
            self._cache_result(event_name, out)
            return out

        # Pattern matching only (no exact match)
        if not self._has_wildcard_patterns:
            return _EMPTY_SUBSCRIBERS

        for pattern, plugin_groups in self._subscribers.items():
            if fnmatch.fnmatch(event_name, pattern):
                for plugin_id, pg in plugin_groups.items():
                    result.extend((cb, ia, plugin_id) for cb, ia in pg)

        if result:
            out = tuple(result)
            self._cache_result(event_name, out)
            return out

        return _EMPTY_SUBSCRIBERS

    def _invalidate_cache(self, event_name: EventName) -> None:
        """Invalidate cache entries that might be affected by changes to event_name."""
        # Remove exact match from cache
        self._exact_match_cache.pop(event_name, None)

        # A pattern change affects many potential matches: clear the whole cache
        if self._has_wildcard_patterns and ("*" in event_name or "?" in event_name):
            self._exact_match_cache.clear()

    def _cache_result(
        self, event_name: EventName, subscribers: tuple[tuple[Callable, bool, PluginId | None], ...]
    ) -> None:
        """Cache the already-built subscriber tuple with simple bounded eviction.

        Stores the exact tuple the caller returns, so the first lookup and every
        cached lookup hand back the same object (no redundant re-allocation).
        """
        if len(self._exact_match_cache) >= self._max_cache_size:
            # FIFO eviction: drop the oldest-inserted entry. dicts preserve
            # insertion order, so next(iter(...)) is the first key in, O(1).
            self._exact_match_cache.pop(next(iter(self._exact_match_cache)), None)
        self._exact_match_cache[event_name] = subscribers

    def mute(self, pattern: str) -> None:
        """Suppress all events whose name matches pattern.

        Pattern syntax is the same as subscription patterns (fnmatch).
        Adding a pattern that is already present is a no-op.
        """
        self._mute_patterns.add(pattern)

    def unmute(self, pattern: str) -> None:
        """Remove a previously added mute pattern.

        Silently does nothing if the pattern was not muted.
        """
        self._mute_patterns.discard(pattern)

    def is_muted(self, name: str) -> bool:
        """Return True if name matches any active mute pattern."""
        return any(fnmatch.fnmatch(name, p) for p in self._mute_patterns)

    def has_subscribers(self, name: str) -> bool:
        """Return True if name has at least one subscriber and is not muted.

        Mute-aware: returns False when the event is muted, so demand-driven
        emitters that check before generating can naturally skip muted topics.
        """
        if self.is_muted(name):
            return False
        return bool(self.get_subscribers(name))

    def count(self) -> int:
        """Get number of active subscriptions."""
        return sum(len(cbs) for group in self._subscribers.values() for cbs in group.values())

    def clear(self) -> None:
        """Clear all subscriptions."""
        self._subscribers.clear()
        self._subscriptions_by_id.clear()
        self._has_wildcard_patterns = False
        self._exact_match_cache.clear()
        self._mute_patterns.clear()
