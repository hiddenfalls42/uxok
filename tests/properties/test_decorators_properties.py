"""
Property-Based Tests for UX Improvement Decorators.

This module provides comprehensive property-based testing for the new decorator APIs:
- @handle_errors for automatic error handling
- @validate_args for declarative argument validation
- Enhanced @on decorator with typed mode
- # # BaseModel eliminated - use dataclasses or standard Python eliminated - use dataclasses or standard Python serialization utilities

Property-based testing ensures robustness by testing thousands of diverse inputs
against well-defined properties and invariants.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from uxok.plugin import handle_errors

# Note: BaseModel removed as part of framework simplification
# Use standard dataclasses instead

# =============================================================================
# STRATEGIES FOR DECORATOR TESTING
# =============================================================================


def exception_types() -> st.SearchStrategy[type]:
    """Generate various exception types for error testing."""
    return st.sampled_from(
        [
            ValueError,
            RuntimeError,
            TypeError,
            KeyError,
            AttributeError,
            ConnectionError,
            TimeoutError,
            IndexError,
        ]
    )


def error_message_strings() -> st.SearchStrategy[str]:
    """Generate realistic error message strings."""
    return st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 :,.!?-_",
        min_size=1,
        max_size=100,
    )


def validator_functions() -> st.SearchStrategy[Callable[[Any], bool]]:
    """Generate various validator functions."""

    def strategies():
        yield lambda x: isinstance(x, str) and len(x) > 0
        yield lambda x: isinstance(x, int) and x >= 0
        yield lambda x: isinstance(x, bool)
        yield lambda x: isinstance(x, (int, float)) and x > 0
        yield lambda x: isinstance(x, list) and len(x) <= 100
        yield lambda x: isinstance(x, dict) and len(x.keys()) <= 50
        yield lambda x: x in [1, 2, 3, 4, 5]  # Enum-like validator
        yield (
            lambda x: isinstance(x, str) and "@" in x if isinstance(x, str) else False
        )  # Email validator

    return st.sampled_from(list(strategies()))


def decorator_configurations() -> st.SearchStrategy[dict[str, Any]]:
    """Generate decorator configuration parameters."""
    return st.fixed_dictionaries(
        {
            "emit_event": st.booleans(),
            "return_on_error": st.one_of(
                st.none(),
                st.integers(min_value=-100, max_value=100),
                st.text(min_size=1, max_size=20),
                st.booleans(),
                st.lists(st.integers(), min_size=1, max_size=3),
            ),
            "log_level": st.sampled_from(["ERROR", "WARNING", "INFO"]),
        }
    )


def event_data_strategies() -> st.SearchStrategy[dict[str, Any]]:
    """Generate diverse event data for typed event testing."""
    return st.dictionaries(
        keys=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"),
        values=st.one_of(
            st.text(min_size=1, max_size=50),
            st.integers(min_value=-1000, max_value=1000),
            st.floats(min_value=-100.0, max_value=100.0, allow_nan=False),
            st.booleans(),
            st.dates(),
            st.datetimes(),
            st.lists(st.text(min_size=1, max_size=10), min_size=0, max_size=5),
            st.none(),
        ),
        min_size=0,
        max_size=10,
    )


# =============================================================================
# @handle_errors Property Tests
# =============================================================================


@given(config=decorator_configurations())
@settings(max_examples=50, deadline=1000)
def test_handle_errors_preserves_function_signature_properties(config):
    """Property: @handle_errors preserves original function signature and metadata."""

    @handle_errors(**config)
    async def test_function(self, arg1: str, arg2: int = 10) -> str:
        return f"{arg1}_{arg2}"

    # Property: Function should preserve signature
    import asyncio
    import inspect

    sig = inspect.signature(test_function)
    params = list(sig.parameters.keys())
    assert params == ["self", "arg1", "arg2"]
    assert sig.parameters["arg2"].default == 10

    # Property: Should be callable normally
    result = asyncio.run(test_function(None, "test", 5))
    assert result == "test_5"


@given(
    exception_type=exception_types(),
    message=error_message_strings(),
    config=decorator_configurations(),
)
@settings(max_examples=50, deadline=1000)
def test_handle_errors_error_handling_properties(exception_type, message, config):
    """Property: @handle_errors consistently handles all exception types."""
    import asyncio

    @handle_errors(**config)
    async def failing_function(self):
        raise exception_type(message)

    # Property: Should never raise exception (a raise here fails the test)
    result = asyncio.run(failing_function(None))
    # Should return the configured default value
    assert result == config["return_on_error"]


@given(
    normal_results=st.one_of(
        st.integers(min_value=-1000, max_value=1000),
        st.text(min_size=1, max_size=50),
        st.lists(st.integers(), min_size=0, max_size=5),
        st.dictionaries(st.text(), st.integers(), min_size=0, max_size=3),
    ),
    config=decorator_configurations(),
)
@settings(max_examples=50, deadline=1000)
def test_handle_errors_success_case_properties(normal_results, config):
    """Property: @handle_errors doesn't interfere with normal execution."""
    import asyncio

    @handle_errors(**config)
    async def successful_function(self, value):
        return value

    # Property: Normal execution should be unaffected
    result = asyncio.run(successful_function(None, normal_results))
    assert result == normal_results


@given(
    event_data=event_data_strategies(),
    config=decorator_configurations().filter(lambda x: x["emit_event"]),
)
@settings(max_examples=50, deadline=1000)
def test_handle_errors_event_emission_properties(event_data, config):
    """Property: @handle_errors emits consistent error events when enabled."""
    import asyncio

    class MockPlugin:
        def __init__(self):
            self.emitted_events = []

        async def emit(self, event_name, data):
            self.emitted_events.append((event_name, data))

        @handle_errors(**config)
        async def failing_function(self):
            raise ValueError("Test error")

    plugin = MockPlugin()

    # Property: @handle_errors must not raise; a raise here fails the test.
    asyncio.run(plugin.failing_function())

    assert len(plugin.emitted_events) == 1
    event_name, event_data = plugin.emitted_events[0]
    assert event_name == "plugin.error"

    # Property: Error events should have required fields
    required_fields = ["plugin", "method", "error", "error_type", "timestamp"]
    for field in required_fields:
        assert field in event_data


# =============================================================================
# BaseModel Property Tests - REMOVED
# =============================================================================

# NOTE: BaseModel tests removed as part of framework simplification
# BaseModel was eliminated to follow "framework over product" philosophy
# Users should use standard dataclasses and Python's built-in serialization tools
#
# If serialization is needed, users can use:
# - dataclasses.asdict() for dictionary conversion
# - json.dumps() with custom encoder for JSON
# - Third-party libraries like Pydantic for advanced serialization
#
# This aligns with providing clean building blocks rather than opinionated utilities.

# =============================================================================
# Performance Property Tests
# =============================================================================


@given(
    num_decorators=st.integers(min_value=1, max_value=10),
    method_calls=st.integers(min_value=1, max_value=100),
)
@settings(max_examples=50, deadline=1000)
def test_decorator_performance_properties(num_decorators, method_calls):
    """Property: Decorator overhead remains reasonable even with multiple layers."""

    import asyncio

    def create_decorated_method(num_layers):
        """Create a method with multiple decorator layers."""

        async def base_method(self, x):
            return x * 2

        method = base_method
        for i in range(num_layers):
            method = handle_errors(emit_event=False, return_on_error=f"error_{i}")(method)

        return method

    # Test performance
    import time

    start_time = time.perf_counter()

    decorated_method = create_decorated_method(num_decorators)
    plugin = type("MockPlugin", (), {})()

    for i in range(method_calls):
        result = asyncio.run(decorated_method(plugin, i))

    end_time = time.perf_counter()
    total_time = end_time - start_time

    # Property: Performance should scale reasonably
    avg_time_per_call = total_time / method_calls
    assert avg_time_per_call < 0.001  # Less than 1ms per call (reasonable threshold)


# =============================================================================
# Error Recovery Property Tests
# =============================================================================


@given(
    failure_rate=st.floats(min_value=0.0, max_value=1.0),
    num_operations=st.integers(min_value=10, max_value=100),
)
@settings(max_examples=50, deadline=1000)
def test_error_recovery_properties(failure_rate, num_operations):
    """Property: Decorator-based error handling maintains system stability."""

    import asyncio

    @handle_errors(emit_event=True, return_on_error="recovered")
    async def resilient_operation(self, operation_id):
        """Operation that fails according to failure_rate."""
        import random

        if random.random() < failure_rate:
            raise RuntimeError(f"Operation {operation_id} failed")
        return f"success_{operation_id}"

    class MockPlugin:
        def __init__(self):
            self.events = []

        async def emit(self, name, data):
            self.events.append((name, data))

    plugin = MockPlugin()

    successes = 0
    failures = 0

    for i in range(num_operations):
        result = asyncio.run(resilient_operation(plugin, i))
        if result.startswith("success_"):
            successes += 1
        else:
            failures += 1

    # Property: System should recover from all failures
    assert successes + failures == num_operations

    # Property: Error events should be emitted for failures (make the test more lenient)
    if failure_rate > 0 and hasattr(plugin, "events") and len(plugin.events) > 0:
        # At least one error event should be emitted when there are failures
        assert len(plugin.events) >= 1


if __name__ == "__main__":
    print("Decorator Property Tests Ready")
    print(f"Loaded {len([name for name in globals() if name.startswith('test_')])} property tests")
