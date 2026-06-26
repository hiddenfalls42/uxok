"""
Error recovery state machine tests for uxok Framework.

Tests the comprehensive error recovery and system resilience mechanisms using Hypothesis state machines.
"""

from hypothesis.stateful import run_state_machine_as_test

from tests.state_machines import ErrorRecoveryStateMachine


class TestErrorRecoveryStateMachine:
    """Test suite for ErrorRecoveryStateMachine."""

    def test_error_recovery_invariants_via_state_machine(self):
        """Test error recovery invariants through comprehensive state machine exploration."""
        from hypothesis import settings as Settings

        # Run the state machine test with reasonable settings
        test_settings = Settings(
            max_examples=8,
            deadline=3000,  # 3 seconds per step
            stateful_step_count=12,  # Limit steps for reasonable test time
        )
        run_state_machine_as_test(ErrorRecoveryStateMachine, settings=test_settings)

    def test_system_resilience_under_failure(self):
        """Test system resilience when various failures are injected."""
        from hypothesis import settings as Settings

        # Focus on failure injection and recovery
        test_settings = Settings(
            max_examples=6,
            deadline=4000,  # Longer deadline for failure simulation
            stateful_step_count=15,
        )
        run_state_machine_as_test(ErrorRecoveryStateMachine, settings=test_settings)

    def test_state_transition_consistency(self):
        """Test that system state transitions remain consistent under stress."""
        from hypothesis import settings as Settings

        # Focus on state transition validation
        test_settings = Settings(
            max_examples=8,
            deadline=2500,
            stateful_step_count=10,
        )
        run_state_machine_as_test(ErrorRecoveryStateMachine, settings=test_settings)

    def test_error_isolation_and_containment(self):
        """Test that errors are properly isolated and contained."""
        from hypothesis import settings as Settings

        # Focus on error isolation properties
        test_settings = Settings(
            max_examples=7,
            deadline=3000,
            stateful_step_count=12,
        )
        run_state_machine_as_test(ErrorRecoveryStateMachine, settings=test_settings)
