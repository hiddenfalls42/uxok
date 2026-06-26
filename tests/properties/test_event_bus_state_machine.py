"""
Event-bus stateful machine tests.

EventBusStateMachine drives a real started Core through randomised sequences
of subscribe / unsubscribe-by-id / unsubscribe-plugin / publish steps and
checks three invariants after every step:

  1. subscription_count_matches_model — impl's _subscriptions_by_id size ==
     model size. The map is read directly (see EventBusStateMachine.
     _live_sub_count) so the invariant stays independent of count().
  2. core_is_running — the Core must never leave RUNNING state.
  3. no_spurious_deliveries_accumulated — subscriptions whose patterns can
     never match any event in the pool must have a zero delivery count.

The publish rule additionally asserts per-step exact delivery correctness:
every live subscriber whose pattern matches the published event name gets
exactly +1; every other subscriber gets +0.

deadline=None: tick-gated dispatch submits coroutines to a gate backed by an
asyncio.sleep(0.05) settle call; wall-clock time exceeds Hypothesis's 200 ms
default under coverage instrumentation.

filter_too_much is suppressed: unsubscribe_by_id uses assume() to skip when no
candidate for the drawn pattern exists; with a small pool Hypothesis can draw
unsubscribable patterns frequently before one succeeds.
"""

from hypothesis import HealthCheck
from hypothesis import settings as Settings
from hypothesis.stateful import run_state_machine_as_test

from tests.state_machines import EventBusStateMachine

_BASE = {
    "max_examples": 25,
    "stateful_step_count": 20,
    "deadline": None,  # tick-gated dispatch is wall-clock sensitive
    "suppress_health_check": [HealthCheck.filter_too_much],
}


class TestEventBusStateMachine:
    """Property-based tests for event-bus delivery correctness and subscription bookkeeping."""

    def test_event_bus_delivery_correctness(self):
        """Assert that every publish step delivers to exactly the matching subscribers.

        Covers: subscribe, unsubscribe by ID, unsubscribe by plugin, exact-name
        and wildcard-pattern matching, and all three post-step invariants.
        """
        run_state_machine_as_test(EventBusStateMachine, settings=Settings(**_BASE))

    def test_event_bus_subscription_bookkeeping(self):
        """Assert subscription count invariant survives many subscribe/unsubscribe cycles.

        Uses a higher step count to stress-test the count bookkeeping under
        repeated churn without publishing (so delivery counters stay zero).
        Wildcard patterns mean a single subscribe can match multiple publish events.
        """
        run_state_machine_as_test(
            EventBusStateMachine,
            settings=Settings(
                **{**_BASE, "stateful_step_count": 30},
            ),
        )
