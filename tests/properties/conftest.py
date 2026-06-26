# pytest configuration for property-based tests
import os
import sys

import pytest

from .hypothesis_config import CI_ENVIRONMENT

# Add the source directory to Python path for testing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def pytest_configure(config):
    """Register custom markers for property-based test types."""
    config.addinivalue_line("markers", "chaos_engineering: marks tests as chaos engineering tests")
    config.addinivalue_line("markers", "performance: marks tests as performance property tests")
    config.addinivalue_line("markers", "scalability: marks tests as scalability/stress tests")
    config.addinivalue_line("markers", "state_machine: marks tests as RuleBasedStateMachine tests")
    config.addinivalue_line("markers", "integration: marks tests as integration property tests")


def pytest_addoption(parser):
    """Add custom command-line options for property testing."""
    parser.addoption(
        "--skip-chaos-tests",
        action="store_true",
        default=False,
        help="Skip chaos engineering tests (useful for quick runs)",
    )
    parser.addoption(
        "--skip-performance-tests",
        action="store_true",
        default=False,
        help="Skip performance property tests",
    )
    parser.addoption(
        "--skip-scalability-tests",
        action="store_true",
        default=False,
        help="Skip scalability/stress tests",
    )


def pytest_collection_finish(session):
    """Print summary after test collection."""
    property_tests = [item for item in session.items if "properties" in str(item.fspath)]
    print(f"\nCollected {len(property_tests)} property-based tests")

    if CI_ENVIRONMENT:
        print("Running in CI environment with optimized settings")


def pytest_runtest_setup(item):
    """Skip marked tests based on command-line options."""
    config = item.config
    skip_chaos = config.getoption("--skip-chaos-tests")
    skip_performance = config.getoption("--skip-performance-tests")
    skip_scalability = config.getoption("--skip-scalability-tests")

    for marker in item.iter_markers():
        if marker.name == "chaos_engineering" and skip_chaos:
            pytest.skip("Chaos engineering tests skipped via command line")
        elif marker.name == "performance" and skip_performance:
            pytest.skip("Performance tests skipped via command line")
        elif marker.name == "scalability" and skip_scalability:
            pytest.skip("Scalability tests skipped via command line")
