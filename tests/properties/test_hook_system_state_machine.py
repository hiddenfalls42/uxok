"""
Hook system stateful machine tests for uxok Framework.

Drives a real _HookSystem through randomised register / unregister / execute /
precache / clear_cache sequences and checks system invariants after every step:
  - get_hooks() stays consistent with the registration-order model
  - the cache, when present, always holds the correct stable-sorted list
  - no phantom hook names accumulate in _hs._hooks

deadline=None: hook callbacks are async coroutines; under coverage instrumentation
they exceed the 200 ms default and trigger endless shrink loops.
"""

from hypothesis import HealthCheck
from hypothesis import settings as Settings
from hypothesis.stateful import run_state_machine_as_test

from tests.state_machines import HookSystemStateMachine

_BASE = {
    "deadline": None,
    "suppress_health_check": [HealthCheck.filter_too_much],
}


class TestHookSystemStateMachine:
    """Test suite for the HookSystemStateMachine stateful property exploration."""

    def test_hook_system_invariants_via_state_machine(self):
        """Full invariant sweep: get_hooks consistency, cache consistency,
        no phantom names — using a balanced mix of all rules.
        """
        run_state_machine_as_test(
            HookSystemStateMachine,
            settings=Settings(max_examples=25, stateful_step_count=20, **_BASE),
        )

    def test_hook_execution_priority_ordering(self):
        """Stress execution-order rule: priority-descending, ties in insertion order.

        More steps so Hypothesis can build up multi-hook queues before firing
        execute_and_check_order.
        """
        run_state_machine_as_test(
            HookSystemStateMachine,
            settings=Settings(max_examples=20, stateful_step_count=30, **_BASE),
        )

    def test_hook_isolation_and_resilience(self):
        """Stress register/unregister symmetry and cache invalidation.

        Reduced step count so Hypothesis samples many independent paths rather
        than building deep per-run state.
        """
        run_state_machine_as_test(
            HookSystemStateMachine,
            settings=Settings(max_examples=30, stateful_step_count=12, **_BASE),
        )

    def test_hook_registry_consistency(self):
        """High step-count run: confirms no phantom names accumulate and
        get_hooks stays consistent across long register/unregister/precache sequences.
        """
        run_state_machine_as_test(
            HookSystemStateMachine,
            settings=Settings(max_examples=15, stateful_step_count=40, **_BASE),
        )
