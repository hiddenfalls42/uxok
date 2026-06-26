"""
Pytest configuration and fixtures for uxok Framework test isolation.

This module provides proper test isolation to prevent state pollution between tests,
which was causing the majority of test failures when running the full test suite.
"""

import asyncio
import gc
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from hypothesis import settings

from uxok import Core
from uxok.protocols import CoreState

# Coverage instrumentation slows the tick system enough to blow Hypothesis's
# default 200ms deadline, sending failing examples into endless shrink loops.
# Run coverage with COVERAGE_RUN=1 to disable deadlines and trim examples.
settings.register_profile("coverage", deadline=None, max_examples=20)
if os.getenv("COVERAGE_RUN"):
    settings.load_profile("coverage")


@pytest_asyncio.fixture(autouse=True)
async def isolate_async_primitives():
    """Isolate async primitives between tests to prevent state pollution.

    This fixture ensures that each test gets a clean async environment,
    preventing event loop and coroutine leakage between tests.
    """
    # Store original state
    original_tasks = asyncio.all_tasks()

    yield

    # Clean up any tasks created during the test
    current_tasks = asyncio.all_tasks()
    for task in current_tasks:
        if task not in original_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, asyncio.InvalidStateError):
                    pass

    # Force garbage collection to clean up any remaining references
    gc.collect()


@pytest_asyncio.fixture
async def clean_core() -> AsyncGenerator[Core, None]:
    """Provide a clean Core instance with guaranteed cleanup.

    This fixture creates a fresh Core instance for each test and ensures
    proper cleanup, preventing state pollution between tests.
    """
    core = Core()

    try:
        yield core
    finally:
        # Ensure the core is properly cleaned up regardless of test outcome
        try:
            if core.state is CoreState.RUNNING:
                await core.stop()
        except Exception:
            # Ignore cleanup errors to prevent test failures
            pass


@pytest_asyncio.fixture
async def started_core(clean_core: Core) -> AsyncGenerator[Core, None]:
    """Provide a started Core instance with guaranteed cleanup.

    This fixture starts the Core instance and ensures it's properly
    cleaned up after each test.
    """
    await clean_core.start()

    try:
        yield clean_core
    finally:
        # Ensure proper cleanup
        if clean_core.state is CoreState.RUNNING:
            await clean_core.stop()


# Configure pytest for better async test handling
def pytest_configure(config):
    """Configure pytest for async testing."""
    # Add custom markers
    config.addinivalue_line("markers", "isolated: marks tests that need complete isolation")
    config.addinivalue_line("markers", "property_test: marks property-based tests")
    config.addinivalue_line("markers", "integration: integration workflow tests")
    config.addinivalue_line("markers", "performance: performance benchmarks (slow)")
    config.addinivalue_line("markers", "concurrency: concurrent operation tests")
    config.addinivalue_line("markers", "chaos: chaos engineering tests")


def pytest_collection_modifyitems(config, items):
    """Apply appropriate marks to tests."""
    for item in items:
        # Mark property tests for special handling
        if "properties/" in str(item.fspath):
            item.add_marker(pytest.mark.property_test)
            item.add_marker(pytest.mark.isolated)


# Performance optimization for CI environments
def pytest_runtest_setup(item):
    """Setup for individual tests."""
    # Skip expensive cleanup for simple tests
    if not any(marker.name in ["property_test", "isolated"] for marker in item.iter_markers()):
        # For simple tests, we can skip some expensive cleanup
        pass
