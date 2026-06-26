"""
Hot-reload stateful machine tests for uxok Framework.

Drives a real started Core through randomised hot-reload sequences -- fresh
loads, good reloads, failed-on_start reloads, probe-event delivery, and
unregistration -- and checks system invariants after every step, including
that every successfully replaced instance had on_stop() called exactly once.

filter_too_much is suppressed because load_fresh uses assume(name not in model).
With 4 names and several already live, Hypothesis often draws a live name for
load_fresh and must filter it.  That is expected and structural, not a problem.
"""

from hypothesis import HealthCheck
from hypothesis import settings as Settings
from hypothesis.stateful import run_state_machine_as_test

from tests.state_machines import HotReloadMachine

_BASE = {
    "max_examples": 10,
    "stateful_step_count": 15,
    "deadline": None,  # tick-gated dispatch is wall-clock sensitive
    "suppress_health_check": [HealthCheck.filter_too_much],
}


class TestHotReloadStateMachine:
    """Test suite for the HotReloadMachine stateful property exploration."""

    def test_hot_reload_invariants_via_state_machine(self):
        """Full invariant sweep: RUNNING state, ID stability, generation
        tracking, capability consistency, empty operation guard, and the
        on_stop-exactly-once contract for replaced instances.
        """
        run_state_machine_as_test(HotReloadMachine, settings=Settings(**_BASE))
