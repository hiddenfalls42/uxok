"""Stateful property machine for FakeTime-driven TickClock + TickScheduler.

Drives the real timing assembly under Hypothesis-controlled sequences of tick
advances, stalls, and schedule registrations.  After every rule the machine
compares an exact pure-Python model against the live clock/scheduler state.

The gate is deleted in the no-gate redesign (decision: concurrent dispatch by
default).  The machine now covers: schedule_at one-shot jobs and tick/stall
counter arithmetic.  Gate-op execution order is no longer modelled.

Async bridge: one event loop per machine instance, rules drive async work via
self._loop.run_until_complete().

HealthCheck.filter_too_much is suppressed: schedule_at uses assume() to guard
cases where the tick is not high enough for a valid target offset — structural,
not a bug.  Mirrors test_reload_state_machine.py's justification.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from hypothesis import HealthCheck
from hypothesis import settings as Settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
    run_state_machine_as_test,
)

from tests.fake_time import FakeTime
from uxok.timing._clock import TickClock
from uxok.timing._scheduler import TickScheduler

# ---------------------------------------------------------------------------
# Model types
# ---------------------------------------------------------------------------


@dataclass
class _AtJob:
    """Model of a one-shot at_tick job."""

    target_tick: int
    fired: bool = False
    actual_fired: list[bool] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Machine
# ---------------------------------------------------------------------------

_TICK_RATE = 100  # Hz


class TimingStateMachine(RuleBasedStateMachine):
    """Stateful machine over a FakeTime-driven TickClock + TickScheduler.

    Model invariants checked after every rule:
    - model_tick == clock.tick
    - each live at_tick job: fired iff target_tick <= clock.tick, and exactly once
      (tasks are fire-and-forget — we drain them after each boundary)
    """

    def __init__(self) -> None:
        super().__init__()
        # One event loop per machine instance.
        self._loop = asyncio.new_event_loop()

        self._fake = FakeTime()
        self._scheduler = TickScheduler()
        self._clock = TickClock(
            tick_rate=_TICK_RATE,
            scheduler=self._scheduler,
            event_bus=None,
            slip_threshold=5,
            precision="sleep",
            catchup="skip",
            time_source=self._fake.monotonic,
            sleep=self._fake.sleep,
        )

        # Start clock (registers the asyncio task on _loop).
        self._loop.run_until_complete(self._start_clock())

        # Model state.
        self._model_tick: int = 0
        self._at_jobs: list[_AtJob] = []

    # ------------------------------------------------------------------
    # Async helpers
    # ------------------------------------------------------------------

    async def _start_clock(self) -> None:
        self._clock.start()
        await asyncio.sleep(0)

    def _run(self, coro):
        """Drive a coroutine to completion on the machine's loop."""
        return self._loop.run_until_complete(coro)

    def _drive_one_tick(self) -> None:
        self._run(self._drive_one_tick_async())

    async def _drive_one_tick_async(self) -> None:
        start = self._clock.tick
        target = start + 1
        max_yields = 500
        yields = 0
        while self._clock.tick < target:
            if yields >= max_yields:
                raise TimeoutError(
                    f"_drive_one_tick: stuck at tick {self._clock.tick} after {max_yields} yields"
                )
            await asyncio.sleep(0)
            yields += 1
            if self._clock.tick >= target:
                break
            deadline = self._fake.next_deadline()
            if deadline is not None:
                dt = max(0.0, deadline - self._fake._now)
                if dt > 0:
                    self._fake.advance(dt)

    async def _drain_scheduler_tasks(self) -> None:
        """Await all in-flight scheduler tasks so at_job fired flags settle."""
        tasks = list(self._scheduler._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Also drain slip_tasks from the clock.
        slip_tasks = list(self._clock._slip_tasks)
        if slip_tasks:
            await asyncio.gather(*slip_tasks, return_exceptions=True)

    async def _park_clock(self) -> None:
        for _ in range(100):
            if self._fake.next_deadline() is not None:
                break
            await asyncio.sleep(0)

    # ------------------------------------------------------------------
    # Model helpers
    # ------------------------------------------------------------------

    def _model_advance(self, new_tick: int) -> None:
        old_tick = self._model_tick
        for job in self._at_jobs:
            if not job.fired and old_tick < job.target_tick <= new_tick:
                job.fired = True
        self._model_tick = new_tick

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def teardown(self) -> None:
        """Stop the clock and close the loop."""
        try:
            self._run(self._clock.stop())
            self._run(self._scheduler.cancel_all())
        finally:
            pending = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                self._run(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    # ------------------------------------------------------------------
    # Initialize
    # ------------------------------------------------------------------

    @initialize()
    def setup(self) -> None:
        """Machine-level initialize hook (state set up in __init__)."""

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    @rule()
    def advance_one_tick(self) -> None:
        """Drive exactly one tick boundary."""
        new_tick = self._model_tick + 1
        self._drive_one_tick()
        # Drain scheduler tasks so at_job fired flags are settled.
        self._run(self._drain_scheduler_tasks())
        self._model_advance(new_tick)

    @rule(k=st.integers(min_value=1, max_value=20))
    def stall(self, k: int) -> None:
        """Inject a stall of K missed tick periods (skip mode: advance k+1)."""
        self._run(self._stall_async(k))
        # Drain tasks launched by the stall boundary.
        self._run(self._drain_scheduler_tasks())
        new_tick = self._model_tick + k + 1
        self._model_advance(new_tick)

    async def _stall_async(self, k: int) -> None:
        await self._park_clock()

        interval = self._clock._tick_interval
        deadline = self._fake.next_deadline()
        pending_sleep = max(0.0, (deadline - self._fake._now) if deadline is not None else 0.0)

        stall_dt = pending_sleep + (k + 0.5) * interval
        self._fake.advance(stall_dt)

        target = self._clock.tick + k + 1
        for _ in range(500):
            await asyncio.sleep(0)
            if self._clock.tick >= target:
                break
            dl = self._fake.next_deadline()
            if dl is not None:
                dt = max(0.0, dl - self._fake._now)
                if dt > 0:
                    self._fake.advance(dt)
        else:
            raise TimeoutError(
                f"_stall_async: clock did not reach tick {target} (stuck at {self._clock.tick})"
            )

    @rule(offset=st.integers(min_value=1, max_value=30))
    def schedule_at(self, offset: int) -> None:
        """Schedule a one-shot job at model_tick + offset."""
        target = self._model_tick + offset
        job = _AtJob(target_tick=target)
        self._at_jobs.append(job)

        # Capture the list reference in the closure for the factory.
        fired_flag = job.actual_fired

        async def factory() -> None:
            fired_flag.append(True)

        self._scheduler.schedule_at(
            tick=target,
            current_tick=self._clock.tick,
            factory=factory,
        )

    # ------------------------------------------------------------------
    # Invariants
    # ------------------------------------------------------------------

    @invariant()
    def tick_counter_matches_model(self) -> None:
        """clock.tick must equal model_tick after every rule."""
        assert self._clock.tick == self._model_tick, (
            f"tick mismatch: clock={self._clock.tick}, model={self._model_tick}"
        )

    @invariant()
    def at_jobs_fire_at_correct_tick(self) -> None:
        """One-shot jobs must fire exactly once when their target_tick has passed.

        - target_tick <= model_tick → fired flag must be True and actual_fired==[True]
        - target_tick > model_tick  → not yet fired
        """
        for job in self._at_jobs:
            if job.target_tick <= self._model_tick:
                assert job.fired, (
                    f"at_job target={job.target_tick} not marked fired at "
                    f"model_tick={self._model_tick}"
                )
                assert len(job.actual_fired) == 1, (
                    f"at_job target={job.target_tick} fired {len(job.actual_fired)} times"
                )
            else:
                assert not job.fired, (
                    f"at_job target={job.target_tick} pre-fired at model_tick={self._model_tick}"
                )
                assert len(job.actual_fired) == 0, (
                    f"at_job target={job.target_tick} ran before target tick"
                )


# ---------------------------------------------------------------------------
# Test entry point
# ---------------------------------------------------------------------------

_BASE_SETTINGS = {
    "max_examples": 50,
    "stateful_step_count": 30,
    "deadline": None,  # FakeTime-driven; wall-clock irrelevant
    "suppress_health_check": [HealthCheck.filter_too_much],
}


class TestTimingStateMachine:
    """Stateful property exploration of the FakeTime-driven timing assembly."""

    def test_timing_invariants_via_state_machine(self) -> None:
        """Full invariant sweep: tick counter and at_tick fire counts under
        randomised advance/stall sequences.
        """
        run_state_machine_as_test(TimingStateMachine, settings=Settings(**_BASE_SETTINGS))
