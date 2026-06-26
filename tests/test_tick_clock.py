"""Tests for TickClock — no-gate free-running metronome.

Test classes:
  TestTickClockLifecycle       — start/stop/idempotency/lock-free read
  TestTickClockCrashGuard      — crash guard logs, signals event, does not hang the clock task
  TestDeterministicCadence     — exact tick counts, no-drift accumulation, smoke
  TestDeterministicSlip        — slip value, reset, threshold gating, payload binding
  TestCatchupSkipDeterministic — skip mode: counter jump, at_tick in gap fires once
  TestCatchupBurstDeterministic — burst mode: replay count, zero sleeps during replay
  TestHybridPrecision          — hybrid busy-wait branch coverage
  TestExtremeRatesDeterministic — constitutional rate bounds (1 Hz, 10 kHz)
"""

from __future__ import annotations

import asyncio

import pytest

from tests.fake_time import FakeTime, run_ticks
from uxok.timing._clock import TickClock
from uxok.timing._scheduler import TickScheduler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_clock(
    fake: FakeTime,
    *,
    rate: int = 100,
    catchup: str = "skip",
    slip_threshold: int = 5,
    precision: str = "sleep",
    busy_wait_us: int = 200,
    scheduler: TickScheduler | None = None,
    bus: object | None = None,
) -> tuple[TickClock, TickScheduler]:
    """Build a FakeTime-driven TickClock with its scheduler."""
    if scheduler is None:
        scheduler = TickScheduler()
    clock = TickClock(
        tick_rate=rate,
        scheduler=scheduler,
        event_bus=bus,
        slip_threshold=slip_threshold,
        precision=precision,
        busy_wait_us=busy_wait_us,
        catchup=catchup,
        time_source=fake.monotonic,
        sleep=fake.sleep,
    )
    return clock, scheduler


async def _stall_clock(
    fake: FakeTime,
    clock: TickClock,
    *,
    k: int,
) -> None:
    """Induce slip of exactly K by advancing past pending sleep + (K+0.5)*interval.

    The half-period margin defeats float truncation flakes.
    """
    interval = clock._tick_interval
    deadline = fake.next_deadline()
    if deadline is None:
        dt = (k + 0.5) * interval
    else:
        dt = max(0.0, deadline - fake._now) + (k + 0.5) * interval
    fake.advance(dt)


# ---------------------------------------------------------------------------
# TestTickClockLifecycle
# ---------------------------------------------------------------------------


class TestTickClockLifecycle:
    """start/stop/idempotency/lock-free read."""

    @pytest.mark.asyncio
    async def test_start_stop_clock(self):
        """Clock increments exactly 5 ticks then freezes after stop().

        After stop() advancing time and yielding must not produce new ticks.
        """
        fake = FakeTime()
        clock, _ = _make_clock(fake, rate=100)

        assert clock.tick == 0
        clock.start()
        await run_ticks(fake, clock, 5)
        assert clock.tick == 5

        await clock.stop()

        # After stop(), no new ticks may appear.
        fake.advance(10 * clock._tick_interval)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert clock.tick == 5

    @pytest.mark.asyncio
    async def test_multiple_start_idempotent(self):
        """Second start() is a no-op: counter unchanged, no second loop spawned.

        Proof: after two run_ticks(3) phases, clock.tick==6 exactly.
        A second loop would double-count; exactly 7 fake.sleep calls confirms
        one loop only (6 completed + 1 pre-parked).
        """
        fake = FakeTime()
        clock, _ = _make_clock(fake, rate=100)

        clock.start()
        await run_ticks(fake, clock, 3)
        assert clock.tick == 3

        clock.start()  # no-op
        assert clock.tick == 3

        await run_ticks(fake, clock, 3)
        assert clock.tick == 6
        # 6 ticks → 7 sleep calls (6 completed + 1 pre-parked).
        assert len(fake.sleep_calls) == 7

        await clock.stop()

    @pytest.mark.asyncio
    async def test_lock_free_tick_read(self):
        """Tick property reads are lock-free integers."""
        clock = TickClock(tick_rate=1000)
        clock.start()
        ticks = [clock.tick for _ in range(100)]
        await clock.stop()
        assert all(isinstance(t, int) for t in ticks)

    @pytest.mark.asyncio
    async def test_stop_then_advance_does_not_resume(self):
        """After stop(), advancing fake time and yielding does not restart the loop."""
        fake = FakeTime()
        clock, _ = _make_clock(fake, rate=100)
        clock.start()
        await run_ticks(fake, clock, 3)
        frozen = clock.tick

        await clock.stop()
        fake.advance(20 * clock._tick_interval)
        for _ in range(10):
            await asyncio.sleep(0)

        assert clock.tick == frozen


# ---------------------------------------------------------------------------
# TestTickClockCrashGuard
# ---------------------------------------------------------------------------


class TestTickClockCrashGuard:
    """Crash in the clock loop logs the failure and publishes core.tick_clock_failed.

    The crash guard fires when an exception propagates out of _run(). To trigger
    it we use FakeTime so the clock can actually tick and reach the scheduler call.
    """

    @pytest.mark.asyncio
    async def test_crash_logs_and_signals_event(self):
        """An exploding scheduler causes the crash guard to fire the failed event.

        Exact key-set: API.md specifies tick_clock_failed carries {tick, tick_rate} only.
        """
        from uxok.events._bus import _EventBus

        class ExplodingScheduler:
            async def tick(self, current_tick: int) -> None:
                raise RuntimeError("scheduler bug")

        fake = FakeTime()
        bus = _EventBus()
        failures: list[object] = []
        signal = asyncio.Event()

        async def on_fail(event: object) -> None:
            failures.append(event)
            signal.set()

        await bus.subscribe("core.tick_clock_failed", on_fail)

        clock = TickClock(
            tick_rate=200,
            scheduler=ExplodingScheduler(),
            event_bus=bus,
            time_source=fake.monotonic,
            sleep=fake.sleep,
        )
        bus._clock = clock  # type: ignore[attr-defined]
        clock.start()

        # Advance time so the clock wakes up and hits the exploding scheduler.
        await asyncio.sleep(0)
        deadline = fake.next_deadline()
        if deadline is not None:
            fake.advance(deadline - fake._now)
        else:
            fake.advance(clock._tick_interval)

        # Pump the event loop until the crash fires and the event is dispatched.
        await asyncio.wait_for(signal.wait(), timeout=2.0)

        # Let dispatch task settle.
        for _ in range(20):
            await asyncio.sleep(0)

        await clock.stop()

        assert len(failures) == 1
        data = failures[0].data  # type: ignore[attr-defined]
        # Exact key-set: API.md specifies {tick, tick_rate} only (no slip).
        assert set(data) == {"tick", "tick_rate"}
        assert isinstance(data["tick"], int)
        assert isinstance(data["tick_rate"], int)
        assert data["tick_rate"] == 200

    @pytest.mark.asyncio
    async def test_crash_stops_clock_running_flag(self):
        """After crash, _running becomes False (loop exits)."""

        class ExplodingScheduler:
            async def tick(self, current_tick: int) -> None:
                raise RuntimeError("crash")

        fake = FakeTime()
        clock = TickClock(
            tick_rate=1000,
            scheduler=ExplodingScheduler(),
            time_source=fake.monotonic,
            sleep=fake.sleep,
        )
        clock.start()

        # Advance to trigger the first tick.
        await asyncio.sleep(0)
        deadline = fake.next_deadline()
        if deadline is not None:
            fake.advance(deadline - fake._now)
        else:
            fake.advance(clock._tick_interval)

        # Pump until the crash is processed.
        for _ in range(100):
            await asyncio.sleep(0)
            if not clock._running:
                break

        assert not clock._running


# ---------------------------------------------------------------------------
# TestDeterministicCadence
# ---------------------------------------------------------------------------


class TestDeterministicCadence:
    """Exact tick counts and cadence invariants driven via FakeTime."""

    @pytest.mark.asyncio
    async def test_exact_tick_count_over_50_boundaries(self):
        """run_ticks(50) yields exactly clock.tick==50 with zero slip throughout."""
        fake = FakeTime()
        clock, _ = _make_clock(fake, rate=100)
        clock.start()

        slip_snapshots: list[int] = []
        for _ in range(50):
            slip_snapshots.append(clock.slip)
            await run_ticks(fake, clock, 1)

        await clock.stop()

        assert clock.tick == 50
        assert all(s == 0 for s in slip_snapshots)
        interval = clock._tick_interval
        assert len(fake.sleep_calls) == 51
        for delay in fake.sleep_calls:
            assert delay == pytest.approx(interval)

    @pytest.mark.asyncio
    async def test_constant_lateness_does_not_accumulate_drift(self):
        """Waking 0.4*interval late every boundary never causes slip.

        Grid anchoring (next_boundary += interval, not actual + interval) means
        sub-period lateness never accumulates.
        """
        fake = FakeTime()
        clock, _ = _make_clock(fake, rate=100, slip_threshold=1)
        interval = clock._tick_interval
        late = 0.4 * interval

        clock.start()

        for _ in range(20):
            await asyncio.sleep(0)
            deadline = fake.next_deadline()
            assert deadline is not None
            fake.advance(deadline - fake._now + late)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        await clock.stop()

        assert clock.tick == 20
        assert clock.slip == 0

    @pytest.mark.asyncio
    async def test_real_clock_smoke_advances(self):
        """SMOKE: real time_source and sleep defaults tick against wall time."""
        clock = TickClock(tick_rate=100)
        clock.start()
        reads: list[int] = []
        for _ in range(5):
            reads.append(clock.tick)
            await asyncio.sleep(0.04)
        await clock.stop()

        assert clock.tick >= 10
        assert all(reads[i] <= reads[i + 1] for i in range(len(reads) - 1))


# ---------------------------------------------------------------------------
# TestDeterministicSlip
# ---------------------------------------------------------------------------


class TestDeterministicSlip:
    """Slip measurement, threshold gating, and payload binding."""

    @pytest.mark.asyncio
    async def test_slip_reports_exact_stall_periods(self):
        """After a K=7 stall in skip mode: clock.slip == 7, clock.tick == 13."""
        fake = FakeTime()
        clock, _ = _make_clock(fake, rate=100, catchup="skip")
        clock.start()

        await run_ticks(fake, clock, 5)
        assert clock.tick == 5

        await _stall_clock(fake, clock, k=7)
        await run_ticks(fake, clock, 1)

        await clock.stop()

        assert clock.tick == 13
        assert clock.slip == 7

    @pytest.mark.asyncio
    async def test_slip_resets_to_zero_after_on_time_boundary(self):
        """One on-time boundary after stall resets slip to 0."""
        fake = FakeTime()
        clock, _ = _make_clock(fake, rate=100, catchup="skip")
        clock.start()

        await run_ticks(fake, clock, 5)
        await _stall_clock(fake, clock, k=7)
        await run_ticks(fake, clock, 1)
        assert clock.slip == 7

        await run_ticks(fake, clock, 1)
        await clock.stop()

        assert clock.slip == 0

    @pytest.mark.asyncio
    async def test_no_slip_event_below_threshold(self):
        """Stall K=7 with threshold=8: zero core.tick_slip events."""
        from uxok.events._bus import _EventBus

        fake = FakeTime()
        bus = _EventBus()
        clock, _ = _make_clock(fake, rate=100, catchup="skip", slip_threshold=8, bus=bus)
        bus._clock = clock  # type: ignore[attr-defined]

        received: list[object] = []

        async def capture(event: object) -> None:
            received.append(event)

        await bus.subscribe("core.tick_slip", capture)

        clock.start()

        await run_ticks(fake, clock, 5)
        await _stall_clock(fake, clock, k=7)
        await run_ticks(fake, clock, 11)

        await clock.stop()

        # Drain any in-flight dispatch tasks.
        for _ in range(20):
            await asyncio.sleep(0)

        assert received == []

    @pytest.mark.asyncio
    async def test_slip_event_payload_binds_slipping_boundary(self):
        """Slip event data is bound at the slipping boundary (tick 13), not at dispatch.

        With fire-and-forget dispatch, the event is created and published at tick 13.
        The subscribe handler runs asynchronously; we wait for it.
        data["tick"] == 13 (the slipping boundary), event.tick == 13 too
        (because the slip event is published AT the slipping boundary's process).
        """
        from uxok.events._bus import _EventBus

        fake = FakeTime()
        bus = _EventBus()
        clock, _ = _make_clock(fake, rate=100, catchup="skip", slip_threshold=5, bus=bus)
        bus._clock = clock  # type: ignore[attr-defined]

        received: list[object] = []
        signal = asyncio.Event()

        async def capture(event: object) -> None:
            received.append(event)
            signal.set()

        await bus.subscribe("core.tick_slip", capture)

        clock.start()

        # 5 clean ticks, then stall K=7.
        await run_ticks(fake, clock, 5)
        await _stall_clock(fake, clock, k=7)
        # Drive the stall boundary (tick 13): slip event launched as task.
        await run_ticks(fake, clock, 1)

        # Wait for the dispatch task to deliver.
        for _ in range(100):
            await asyncio.sleep(0)
            if received:
                break

        await clock.stop()

        assert len(received) == 1
        event = received[0]
        # Data is bound at the slipping boundary.
        assert event.data == {"tick": 13, "slip": 7, "tick_rate": 100}  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# TestCatchupSkipDeterministic
# ---------------------------------------------------------------------------


class TestCatchupSkipDeterministic:
    """Skip catchup: tick counter jumps K+1 at the stall boundary."""

    @pytest.mark.asyncio
    async def test_skip_jumps_counter_by_exactly_k_plus_one(self):
        """skip catchup: pre-stall tick 5, K=7 → clock.tick == 13."""
        fake = FakeTime()
        clock, _ = _make_clock(fake, rate=100, catchup="skip")
        clock.start()

        await run_ticks(fake, clock, 5)
        assert clock.tick == 5

        await _stall_clock(fake, clock, k=7)
        await run_ticks(fake, clock, 1)

        await clock.stop()

        assert clock.tick == 13

    @pytest.mark.asyncio
    async def test_skip_fires_at_tick_in_gap_exactly_once_late(self):
        """schedule_at(8) inside the 5→13 gap fires exactly once at the jump boundary.

        Range-based due-collection: entries in (last=5, current=13] fire once.
        """
        fake = FakeTime()
        scheduler = TickScheduler()
        clock, _ = _make_clock(fake, rate=100, catchup="skip", scheduler=scheduler)

        fire_count = 0
        fired_event = asyncio.Event()

        async def count() -> None:
            nonlocal fire_count
            fire_count += 1
            fired_event.set()

        clock.start()

        await run_ticks(fake, clock, 5)
        scheduler.schedule_at(8, clock.tick, lambda: count())

        await _stall_clock(fake, clock, k=7)
        await run_ticks(fake, clock, 1)

        # Wait for the scheduled task to complete.
        for _ in range(100):
            await asyncio.sleep(0)
            if fire_count == 1:
                break

        # Drive more ticks to confirm no repeat.
        await run_ticks(fake, clock, 5)

        await clock.stop()

        assert fire_count == 1


# ---------------------------------------------------------------------------
# TestCatchupBurstDeterministic
# ---------------------------------------------------------------------------


class TestCatchupBurstDeterministic:
    """Burst catchup: each missed boundary replayed individually."""

    @pytest.mark.asyncio
    async def test_burst_replays_exactly_the_missed_boundaries(self):
        """K=7: 8 boundary firings (ticks 6..13), zero fake sleeps during replay,
        terminal clock.tick == 13.
        """
        fake = FakeTime()
        clock, _ = _make_clock(fake, rate=100, catchup="burst")
        clock.start()

        await run_ticks(fake, clock, 5)
        assert clock.tick == 5

        sleep_count_before = len(fake.sleep_calls)

        await _stall_clock(fake, clock, k=7)
        await run_ticks(fake, clock, 8)

        sleep_count_after = len(fake.sleep_calls)

        await clock.stop()

        assert clock.tick == 13
        # 8 replay boundaries have sleep_for < 0 → no sleep call during replay.
        # After tick 13, the clock parks once for tick 14: net +1 sleep call.
        assert sleep_count_after == sleep_count_before + 1

    @pytest.mark.asyncio
    async def test_burst_slip_event_sequence(self):
        """threshold=5: slip events during burst replay are [7, 6, 5] in order.

        Each scheduled task is fire-and-forget; we gather them to confirm order.
        """
        from uxok.events._bus import _EventBus

        fake = FakeTime()
        bus = _EventBus()
        clock, _ = _make_clock(fake, rate=100, catchup="burst", slip_threshold=5, bus=bus)
        bus._clock = clock  # type: ignore[attr-defined]

        slip_values: list[int] = []

        async def capture(event: object) -> None:
            slip_values.append(event.data["slip"])  # type: ignore[attr-defined]

        await bus.subscribe("core.tick_slip", capture)

        clock.start()

        await run_ticks(fake, clock, 5)
        await _stall_clock(fake, clock, k=7)
        # 8 replay boundaries + extra ticks to flush slip event tasks.
        await run_ticks(fake, clock, 10)

        # Drain any in-flight tasks.
        for _ in range(50):
            await asyncio.sleep(0)

        await clock.stop()

        assert slip_values == [7, 6, 5]


# ---------------------------------------------------------------------------
# TestHybridPrecision
# ---------------------------------------------------------------------------


class TestHybridPrecision:
    """Covers the hybrid busy-wait branch (_clock.py), previously untested."""

    @pytest.mark.asyncio
    async def test_hybrid_sleeps_short_then_busy_waits_to_boundary(self):
        """hybrid: sleep shortened by busy_window; spin loop advances monotonic.

        With FakeTime(auto_advance=1e-4), each monotonic() call nudges time,
        so the spin exits after ~20 iterations per boundary.
        """
        busy_wait_us = 2000  # 0.002 s
        rate = 100  # interval = 0.01 s
        fake = FakeTime(auto_advance=1e-4)
        clock, _ = _make_clock(fake, rate=rate, precision="hybrid", busy_wait_us=busy_wait_us)
        interval = clock._tick_interval
        busy_sec = busy_wait_us / 1_000_000

        original_monotonic = fake.monotonic
        call_count = [0]

        def counting_monotonic() -> float:
            call_count[0] += 1
            return original_monotonic()

        clock._time = counting_monotonic

        clock.start()
        await run_ticks(fake, clock, 3)
        await clock.stop()

        assert clock.tick == 3

        epsilon = 1e-9
        for delay in fake.sleep_calls:
            assert delay <= interval - busy_sec + epsilon

        # Evidence of spinning: more than 2 monotonic calls per boundary.
        assert call_count[0] > 3 * 2

    @pytest.mark.asyncio
    async def test_hybrid_remainder_below_busy_window_sleeps_directly(self):
        """hybrid: busy_wait_us >= interval → plain sleep branch (no spin)."""
        busy_wait_us = 50_000  # 0.05 s > 0.01 s interval
        rate = 100
        fake = FakeTime()
        clock, _ = _make_clock(fake, rate=rate, precision="hybrid", busy_wait_us=busy_wait_us)
        interval = clock._tick_interval

        clock.start()
        await run_ticks(fake, clock, 5)
        await clock.stop()

        assert clock.tick == 5
        for delay in fake.sleep_calls:
            assert delay == pytest.approx(interval, abs=1e-9)


# ---------------------------------------------------------------------------
# TestExtremeRatesDeterministic
# ---------------------------------------------------------------------------


class TestExtremeRatesDeterministic:
    """Constitutional rate boundaries: 1 Hz (floor) and 10 000 Hz (ceiling)."""

    @pytest.mark.asyncio
    async def test_one_hz_clock_exact(self):
        """1 Hz: 3 boundaries → tick==3, each sleep≈1.0 s."""
        fake = FakeTime()
        clock, _ = _make_clock(fake, rate=1)
        clock.start()
        await run_ticks(fake, clock, 3)
        await clock.stop()

        assert clock.tick == 3
        assert len(fake.sleep_calls) == 4
        for delay in fake.sleep_calls:
            assert delay == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_ten_khz_clock_exact(self):
        """10 000 Hz (constitutional max): 10 boundaries → tick==10, slip==0."""
        fake = FakeTime()
        clock, _ = _make_clock(fake, rate=10_000)
        clock.start()
        await run_ticks(fake, clock, 10)
        await clock.stop()

        assert clock.tick == 10
        assert clock.slip == 0
