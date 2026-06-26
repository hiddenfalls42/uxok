"""Minimal async-safe primitives for atomic operations in uxok Framework.

Provides an async-safe set used for race-free tracking of in-flight operations
(e.g. the core's per-plugin operation guard). Atomic compound operations
eliminate TOCTOU (Time-Of-Check-Time-Of-Use) races.

Example Usage:
    >>> active = _AsyncSafeSet()
    >>> if await active.add(plugin_id):
    ...     ...  # we won the race to operate on this plugin
    >>> await active.remove(plugin_id)
"""

import asyncio

__all__ = [
    "_AsyncSafeSet",
]


class _AsyncSafeSet[T]:
    """Thread-safe, async-safe set with atomic operations.

    Provides atomic set operations for race-free concurrent access. Used for
    tracking in-flight lifecycle operations so the same plugin cannot be
    operated on twice concurrently.

    Example:
        >>> active = _AsyncSafeSet()
        >>> await active.add(item)
        >>> await active.remove(item)
    """

    def __init__(self, initial: set[T] | None = None) -> None:
        self._data: set[T] = initial or set()
        self._lock = asyncio.Lock()

    async def add(self, item: T) -> bool:
        """Add item to set. Returns True if item was added (False if present)."""
        async with self._lock:
            if item in self._data:
                return False
            self._data.add(item)
            return True

    async def remove(self, item: T) -> bool:
        """Remove item from set. Returns True if item was removed."""
        async with self._lock:
            if item in self._data:
                self._data.remove(item)
                return True
            return False

    async def clear(self) -> None:
        """Clear all items."""
        async with self._lock:
            self._data.clear()

    async def copy(self) -> set[T]:
        """Get copy of set."""
        async with self._lock:
            return self._data.copy()

    def __repr__(self) -> str:
        return f"_AsyncSafeSet(items={len(self._data)})"
