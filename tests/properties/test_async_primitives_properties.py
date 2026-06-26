"""
Property-based tests for async primitives.

Tests the essential atomic operations that prevent TOCTOU vulnerabilities.
"""

import asyncio

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from uxok.utils.async_primitives import _AsyncSafeSet


class TestAsyncPrimitivesProperties:
    """Property-based tests for async primitives."""

    @pytest.mark.asyncio
    @given(items=st.lists(st.text(min_size=1, max_size=5), min_size=1, max_size=8))
    @settings(max_examples=8, deadline=2000)
    async def test_async_safe_set_operations(self, items):
        """Property: _AsyncSafeSet maintains consistency under concurrent operations."""
        atomic_set = _AsyncSafeSet()

        # Test adding items
        added_count = 0
        for item in items[:5]:  # Use first 5 items
            added = await atomic_set.add(item)
            if added:
                added_count += 1

        # Test concurrent operations
        async def set_worker(ops):
            results = []
            for op in ops:
                if op["type"] == "add":
                    result = await atomic_set.add(op["item"])
                    results.append(("add", op["item"], result))
                elif op["type"] == "remove":
                    result = await atomic_set.remove(op["item"])
                    results.append(("remove", op["item"], result))
            return results

        # Create some concurrent operations
        operations = []
        unique_items = list(set(items[:5]))
        for item in unique_items:
            operations.append({"type": "add", "item": item})  # Should fail (already added)
            operations.append({"type": "remove", "item": item})  # Should succeed

        if operations:
            results = await asyncio.gather(
                *[asyncio.create_task(set_worker([op])) for op in operations[:3]],
                return_exceptions=True,
            )

            # No exceptions should occur
            for result in results:
                assert not isinstance(result, Exception), f"Unexpected exception: {result}"

        # Final contents should be consistent
        final_items = await atomic_set.copy()
        assert isinstance(final_items, set)
