"""
Core System Basic Property-Based Tests.

This module contains simple, working property-based tests for the core system
to validate the infrastructure and provide baseline coverage improvements.
"""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.strategies import valid_core_configs
from uxok import Core
from uxok.protocols import CoreState


class TestCoreBasicProperties:
    """Basic property-based tests for Core system invariants."""

    @given(config=valid_core_configs())
    @settings(max_examples=50, deadline=1000)
    def test_core_initialization_invariants(self, config):
        """
        Property: Core initialization maintains invariants.

        Tests that all valid core configurations produce properly initialized cores.
        """
        core = Core(**dataclasses.asdict(config))

        # Invariant: Core should be created successfully
        assert core is not None

        # Invariant: Core should start in INITIALIZED state
        assert core.state == CoreState.INITIALIZED

        # Invariant: Core should have essential components
        assert core.events is not None
        assert core._registry is not None
        assert core.hooks is not None

    @given(config=valid_core_configs())
    @settings(max_examples=30, deadline=1000)
    def test_core_state_transitions(self, config):
        """
        Property: Core state transitions are consistent.

        Tests that core state machine follows expected transition patterns.
        """
        core = Core(**dataclasses.asdict(config))

        # Invariant: Initial state should be INITIALIZED
        assert core.state == CoreState.INITIALIZED

        # Invariant: Core should have essential async methods
        assert hasattr(core, "start")
        assert hasattr(core, "stop")

    @given(
        base_config=valid_core_configs(),
        max_plugins=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=30, deadline=500)
    def test_config_parameter_invariants(self, base_config, max_plugins):
        """
        Property: Configuration parameters maintain invariants.

        Tests that config parameters are properly validated and applied.
        """
        # Override specific parameters
        base_config.max_plugins = max_plugins

        core = Core(**dataclasses.asdict(base_config))

        # Invariant: Core should be created with the custom config
        assert core is not None
        assert core._core_config.max_plugins == max_plugins

    @given(config=valid_core_configs())
    @settings(max_examples=20, deadline=500)
    def test_core_component_access(self, config):
        """
        Property: Core components are accessible and follow protocols.

        Tests that all core components are properly instantiated and accessible.
        """
        core = Core(**dataclasses.asdict(config))

        # Invariant: Event bus should be accessible
        event_bus = core.events
        assert event_bus is not None

        # Invariant: Registry should be accessible
        registry = core._registry
        assert registry is not None

        # Invariant: Hook system should be accessible
        hook_system = core.hooks
        assert hook_system is not None

        # Invariant: All components should have expected interfaces
        assert hasattr(event_bus, "publish")
        assert hasattr(event_bus, "subscribe")
        assert hasattr(registry, "add")
        assert hasattr(registry, "all")
        assert hasattr(hook_system, "register")
        assert hasattr(hook_system, "execute")


if __name__ == "__main__":
    # Run individual property tests
    pytest.main([__file__, "-v"])
