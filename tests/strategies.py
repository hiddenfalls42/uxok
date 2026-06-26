"""
Hypothesis strategies for uxok Framework property-based testing.

This module provides the data-generation strategies actually consumed by the
test suite: core configurations, capability sets, event/hook names, events,
and chaos failure rates.
"""

from __future__ import annotations

import hypothesis.strategies as st

from uxok.protocols import CoreConfig, Event


def _core_configs(max_plugins: st.SearchStrategy[int]) -> st.SearchStrategy[CoreConfig]:
    """Build CoreConfig instances with the given max_plugins strategy."""
    return st.builds(CoreConfig, max_plugins=max_plugins)


def valid_core_configs() -> st.SearchStrategy[CoreConfig]:
    """Generate valid CoreConfig instances for property-based testing."""
    return _core_configs(st.integers(min_value=10, max_value=100))


def edge_case_configs() -> st.SearchStrategy[CoreConfig]:
    """Generate CoreConfig instances at the edges (minimal, huge, perf-sized)."""
    return _core_configs(st.sampled_from([10, 1000, 10000]))


def capability_sets() -> st.SearchStrategy[set[str]]:
    """Generate capability sets for plugins."""
    common_capabilities = [
        "database",
        "streaming",
        "metrics",
        "logging",
        "auth",
        "cache",
        "queue",
        "storage",
        "network",
        "security",
        "monitoring",
        "tracing",
        "messaging",
        "scheduler",
        "web",
    ]

    return st.sets(
        st.sampled_from(common_capabilities), min_size=0, max_size=len(common_capabilities)
    )


def plugin_capability_combinations() -> st.SearchStrategy[tuple[set[str], set[str]]]:
    """Generate plugin capability (provides, requires) combinations."""
    return st.tuples(capability_sets(), capability_sets())


def hook_names() -> st.SearchStrategy[str]:
    """Generate valid hook names."""
    actions = ["before_", "after_", "on_", "process_", "handle_", "validate_"]
    objects = ["plugin", "event", "config", "startup", "shutdown", "error", "data"]

    return st.builds(
        lambda action, obj: f"{action}{obj}",
        action=st.sampled_from(actions),
        obj=st.sampled_from(objects),
    )


def event_names() -> st.SearchStrategy[str]:
    """Generate valid event names."""
    categories = ["system", "plugin", "user", "error", "debug"]
    actions = ["started", "stopped", "created", "deleted", "updated", "failed"]

    return st.builds(
        lambda cat, act: f"{cat}.{act}",
        cat=st.sampled_from(categories),
        act=st.sampled_from(actions),
    )


def priority_levels() -> st.SearchStrategy[int]:
    """Generate priority levels for hooks/events."""
    return st.integers(min_value=0, max_value=1000)


def valid_events() -> st.SearchStrategy[Event]:
    """Generate valid Event instances."""
    return st.builds(
        Event,
        name=st.text(min_size=1, max_size=50).filter(lambda x: x.strip()),
        data=st.dictionaries(
            keys=st.text(min_size=1, max_size=20),
            values=st.one_of(
                st.text(),
                st.integers(),
                st.floats(),
                st.booleans(),
                st.lists(st.text()),
                st.none(),
            ),
        ),
        timestamp=st.floats(min_value=0.0, max_value=1e9, allow_infinity=False, allow_nan=False),
        tick=st.integers(min_value=0, max_value=10000),
        slip=st.integers(min_value=0, max_value=100),
    )


def failure_rates() -> st.SearchStrategy[float]:
    """Generate realistic failure rates (0.05-0.4) for chaos testing."""
    return st.floats(min_value=0.05, max_value=0.4, allow_nan=False, allow_infinity=False)
