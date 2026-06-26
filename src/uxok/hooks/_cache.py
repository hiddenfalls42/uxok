"""Hook cache with None-sentinel invalidation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uxok.protocols import Hook
    from uxok.protocols._types import HookName


class HookCache:
    """Cache for sorted hook lists.

    Entry absent or None  → cache miss (invalid / never cached / invalidated)
    Entry is a list       → cache hit (valid sorted hooks)

    No locks. All access is on the asyncio event loop thread.
    Invalidation is O(1) deletion. Population is O(1) assignment.
    """

    def __init__(self) -> None:
        # {hook_name: list[tuple[int, Hook]] | None}
        self._cache: dict[str, list[tuple[int, Hook]] | None] = {}

    def get_cached_hooks(self, hook_name: HookName) -> list[tuple[int, Hook]] | None:
        """Return cached sorted hooks, or None on cache miss."""
        return self._cache.get(hook_name)  # absent → None; None → None; list → list

    def cache_hooks(self, hook_name: HookName, hooks: list[tuple[int, Hook]]) -> None:
        """Store sorted hooks. Overwrites any existing entry including None."""
        self._cache[hook_name] = hooks

    def invalidate_cache(self, hook_name: HookName) -> None:
        """Mark hook_name as invalid. Next get_cached_hooks returns None."""
        self._cache.pop(hook_name, None)  # absent is already invalid; no-op either way

    def clear_cache(self) -> None:
        """Invalidate all entries."""
        self._cache.clear()
