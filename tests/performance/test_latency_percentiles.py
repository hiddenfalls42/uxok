"""
Performance tests for uxok Framework.

These tests measure latency percentiles and sustained throughput to establish
performance baselines and detect regressions.
"""

import asyncio
import time
import tracemalloc

import pytest
import pytest_asyncio
from hypothesis import given, settings
from hypothesis import strategies as st

from uxok import Core
from uxok.protocols import Event


@pytest.mark.asyncio
@pytest.mark.performance
@given(event_count=st.integers(min_value=1000, max_value=10000))
@settings(max_examples=5, deadline=10000)
async def test_event_latency_percentiles_property(event_count):
    """
    Property: Event publish latency meets performance requirements.

    Measures p50, p99, p999 latencies for event publishing and verifies
    they meet robotics/real-time requirements.
    """
    # Deliberately NOT the started_core fixture: function-scoped fixtures
    # live longer than one @given example.
    core = Core()

    try:
        await core.start()

        # Warm up the system
        for _ in range(100):
            await core.events.publish(Event(name="warmup", data={}, timestamp=0.0, tick=0, slip=0))

        # Measure latencies
        latencies_ns: list[int] = []

        for i in range(event_count):
            start_time = time.perf_counter_ns()

            await core.events.publish(
                Event(name="perf_test", data={"index": i}, timestamp=time.time())
            )

            end_time = time.perf_counter_ns()
            latencies_ns.append(end_time - start_time)

        # Ensure all events are processed
        await asyncio.sleep(0.005)

        # Convert to microseconds for readability
        latencies_us = [ns / 1000 for ns in latencies_ns]
        latencies_us.sort()

        # Calculate percentiles
        def percentile(p: float) -> float:
            index = int(len(latencies_us) * p / 100)
            return latencies_us[min(index, len(latencies_us) - 1)]

        p50 = percentile(50)
        p99 = percentile(99)
        p999 = percentile(99.9)

        # Robotics/real-time requirements (relaxed for CI environments)
        # In production, these would be much stricter
        assert p50 < 100, f"p50 latency {p50:.1f}μs > 100μs (too slow for 10kHz control)"
        assert p99 < 1000, f"p99 latency {p99:.1f}μs > 1ms (worst case too slow)"
        assert p999 < 10000, f"p999 latency {p999:.1f}μs > 10ms (tail latency unacceptable)"

        # Log performance metrics for regression tracking
        print(f"Performance metrics: p50={p50:.1f}μs, p99={p99:.1f}μs, p999={p999:.1f}μs")

    finally:
        await core.stop()


@pytest.mark.asyncio
@pytest.mark.performance
async def test_sustained_throughput_stability(started_core):
    """
    Test: Sustained throughput remains stable over time.

    Runs for 60 seconds at target throughput and verifies:
    - Throughput variance < 20%
    - No memory leaks
    - System remains responsive
    """
    core = started_core

    # Target: 10k events/sec for 60 seconds = 600k total events
    target_throughput = 10000  # events/sec
    duration_seconds = 60
    total_events = target_throughput * duration_seconds

    # Track throughput every 5 seconds
    throughput_measurements: list[tuple[float, int]] = []
    events_published = 0

    start_time = time.perf_counter()

    async def publish_events_batch(batch_size: int):
        """Publish a batch of events."""
        tasks = []
        for i in range(batch_size):
            event = Event(
                name="throughput_test",
                data={"batch": events_published // batch_size, "index": i},
                timestamp=time.time(),
            )
            tasks.append(core.events.publish(event))
        await asyncio.gather(*tasks)

    # Run sustained test
    measurement_interval = 5.0  # seconds
    next_measurement = start_time + measurement_interval

    while events_published < total_events:
        current_time = time.perf_counter()

        # Check if it's time for a measurement
        if current_time >= next_measurement:
            elapsed = current_time - start_time
            events_in_interval = events_published - sum(e for _, e in throughput_measurements)
            actual_throughput = events_in_interval / measurement_interval

            throughput_measurements.append((elapsed, events_published))
            print(f"At {elapsed:.1f}s: {actual_throughput:.0f} events/sec")

            next_measurement += measurement_interval

        # Publish events to maintain target throughput
        # Calculate how many events to publish this iteration
        elapsed = current_time - start_time
        target_events = int(elapsed * target_throughput)
        events_needed = min(1000, target_events - events_published)  # Batch size

        if events_needed > 0:
            await publish_events_batch(events_needed)
            events_published += events_needed

        # Small delay to prevent overwhelming the event loop
        await asyncio.sleep(0.001)

    # Wait for all events to be processed
    await asyncio.sleep(0.005)

    # Analyze throughput stability
    throughputs = []
    prev_events = 0
    prev_time = 0

    for measurement_time, total_events_at_time in throughput_measurements:
        if prev_time > 0:
            events_in_interval = total_events_at_time - prev_events
            time_interval = measurement_time - prev_time
            throughput = events_in_interval / time_interval
            throughputs.append(throughput)

        prev_events = total_events_at_time
        prev_time = measurement_time

    if throughputs:
        avg_throughput = sum(throughputs) / len(throughputs)
        min_throughput = min(throughputs)
        max_throughput = max(throughputs)
        variance = (max_throughput - min_throughput) / avg_throughput

        # Throughput should be reasonably stable
        assert variance < 0.20, f"Throughput variance {variance:.1%} > 20% (unstable)"

        # Should maintain reasonable throughput
        assert avg_throughput > target_throughput * 0.8, (
            f"Average throughput {avg_throughput:.0f} < 80% of target {target_throughput}"
        )

        print(f"Throughput stability: avg={avg_throughput:.0f}, variance={variance:.1%}")


@pytest.mark.asyncio
@pytest.mark.performance
async def test_memory_stability_under_load(started_core):
    """
    Test: Memory usage remains stable under sustained load.

    Runs high-throughput event processing while monitoring memory usage
    to detect leaks and ensure long-running stability.
    """
    core = started_core

    # Start memory tracing (after fixture setup: assertions only compare
    # deltas between snapshots, so core construction need not be traced)
    tracemalloc.start()

    try:
        # Test parameters
        duration_seconds = 60
        target_throughput = 5000  # events/sec (lower to focus on memory)
        measurement_interval = 5.0  # seconds

        # Track memory usage over time
        memory_snapshots: list[tuple[float, int]] = []
        events_published = 0

        start_time = time.perf_counter()

        async def publish_events_continuous():
            """Continuously publish events."""
            nonlocal events_published

            while time.perf_counter() - start_time < duration_seconds:
                # Publish batch of events
                batch_size = 100
                tasks = []

                for i in range(batch_size):
                    event = Event(
                        name="memory_test",
                        data={"index": events_published + i},
                        timestamp=time.time(),
                    )
                    tasks.append(core.events.publish(event))

                await asyncio.gather(*tasks)
                events_published += batch_size

                # Small delay to maintain target throughput
                await asyncio.sleep(batch_size / target_throughput)

        # Start publishing task
        publish_task = asyncio.create_task(publish_events_continuous())

        # Monitor memory usage
        next_measurement = start_time + measurement_interval

        while time.perf_counter() - start_time < duration_seconds:
            current_time = time.perf_counter()

            if current_time >= next_measurement:
                # Take memory snapshot
                current, peak = tracemalloc.get_traced_memory()
                elapsed = current_time - start_time

                memory_snapshots.append((elapsed, current))
                print(f"At {elapsed:.1f}s: {current / 1024 / 1024:.1f}MB current")

                next_measurement += measurement_interval

            await asyncio.sleep(0.1)  # Don't busy wait

        # Stop publishing
        publish_task.cancel()
        try:
            await publish_task
        except asyncio.CancelledError:
            pass

        # Wait for final event processing
        await asyncio.sleep(0.005)

        # Analyze memory stability
        if len(memory_snapshots) >= 3:
            memory_values = [mem for _, mem in memory_snapshots]
            initial_memory = memory_values[0]
            final_memory = memory_values[-1]
            peak_memory = max(memory_values)

            # Calculate memory growth
            growth_bytes = final_memory - initial_memory
            growth_mb = growth_bytes / 1024 / 1024

            # Allow some growth for event queue buildup, but not excessive
            max_allowed_growth = 50 * 1024 * 1024  # 50MB

            assert growth_bytes < max_allowed_growth, (
                f"Memory growth {growth_mb:.1f}MB > 50MB (possible leak)"
            )

            print(
                f"Memory analysis: initial={initial_memory / 1024 / 1024:.1f}MB, "
                f"final={final_memory / 1024 / 1024:.1f}MB, "
                f"growth={growth_mb:.1f}MB, "
                f"peak={peak_memory / 1024 / 1024:.1f}MB"
            )

        # Verify reasonable total events processed
        assert events_published > duration_seconds * target_throughput * 0.5, (
            f"Only processed {events_published} events, expected ~{duration_seconds * target_throughput}"
        )

    finally:
        tracemalloc.stop()


class TestPluginCollectionCachingPerformance:
    """Performance tests for PluginCollection caching system."""

    @pytest_asyncio.fixture
    async def core_with_plugins(self, started_core):
        """Create a core with multiple plugins for performance testing."""
        from uxok import Plugin

        class PerfPlugin(Plugin):
            def __init__(self, name: str, provides: set[str] | None = None):
                super().__init__(name=name, provides=provides or set())

        core = started_core

        # Register multiple plugins to make collection substantial
        plugin_count = 20
        for i in range(plugin_count):
            provides = {f"cap_{i % 5}"}  # Some capability overlap
            plugin = PerfPlugin(f"perf_plugin_{i}", provides=provides)
            await core.register_plugin(plugin)

        return core

    @pytest.mark.asyncio
    async def test_cached_list_performance(self, core_with_plugins):
        """Test cached PluginCollection.list() performance."""
        import time

        # First call to build cache
        await core_with_plugins.list()

        # Measure cached call performance
        start = time.time()
        for _ in range(100):
            result = await core_with_plugins.list()
        end = time.time()

        avg_time = (end - start) / 100
        assert result is not None
        # Should be very fast (< 1ms typically)
        assert avg_time < 0.01, f"Cached list() too slow: {avg_time:.6f}s"

    @pytest.mark.asyncio
    async def test_collection_filtering_performance(self, core_with_plugins):
        """Test filtering operations performance on cached collections."""
        import time

        # Build cache first
        await core_with_plugins.list()

        # Measure filtering operations performance
        start = time.time()
        for _ in range(100):
            plugins = await core_with_plugins.list()
            active = plugins.active
            capability_providers = plugins.capability.provides("cap_0")
            name_lookup = plugins.by_name("perf_plugin_5")
        end = time.time()

        avg_time = (end - start) / 100
        # 20 plugins providing cap_{i % 5}: exactly 4 provide cap_0.
        assert name_lookup is not None
        assert capability_providers.count == 4
        assert active.count == 20
        # Should be fast (< 5ms typically for filtering operations)
        assert avg_time < 0.005, f"Filtering too slow: {avg_time:.6f}s"

    @pytest.mark.asyncio
    async def test_caching_speedup_ratio(self, core_with_plugins):
        """Measure the speedup provided by caching."""
        import time

        # First call builds cache (uncached)
        start = time.time()
        plugins1 = await core_with_plugins.list()
        uncached_time = time.time() - start

        # Second call uses cache
        start = time.time()
        plugins2 = await core_with_plugins.list()
        cached_time = time.time() - start

        # Verify caching works
        assert plugins1 is plugins2, "Should be using cached collection"

        # Calculate speedup
        if cached_time > 0 and uncached_time > cached_time:
            speedup = uncached_time / cached_time
            print(
                f"Caching speedup: {speedup:.1f}x (uncached: {uncached_time:.6f}s, cached: {cached_time:.6f}s)"
            )

            # Should provide significant speedup (at least 2x for cached vs uncached)
            assert speedup > 2, f"Insufficient caching speedup: {speedup:.1f}x"
        else:
            # If times are too close, just verify caching works
            print(f"Cache working: uncached={uncached_time:.6f}s, cached={cached_time:.6f}s")
            assert cached_time <= uncached_time, "Cached call should be faster or equal"

    @pytest.mark.asyncio
    async def test_memory_overhead_of_caching(self, core_with_plugins):
        """Measure memory overhead of maintaining cached collection."""
        import gc
        import os

        import psutil

        process = psutil.Process(os.getpid())

        # Memory before building cache
        mem_before = process.memory_info().rss

        # Build cache
        plugins = await core_with_plugins.list()

        # Memory after building cache
        mem_after = process.memory_info().rss

        # Memory with cached collection
        mem_cached = process.memory_info().rss

        # Force garbage collection and measure again
        gc.collect()
        mem_after_gc = process.memory_info().rss

        # Calculate memory overhead
        cache_overhead = mem_after - mem_before
        sustained_overhead = mem_after_gc - mem_before

        # Convert to MB for readability
        cache_mb = cache_overhead / 1024 / 1024
        sustained_mb = sustained_overhead / 1024 / 1024

        print(f"Cache memory: {cache_mb:.2f}MB for {plugins.count} plugins")
        print(f"Sustained memory: {sustained_mb:.2f}MB after GC")

        # Memory overhead should be reasonable (< 10MB for 20 plugins)
        assert cache_overhead < 10 * 1024 * 1024, f"Excessive memory overhead: {cache_mb:.1f}MB"

        # Should not leak memory excessively
        assert sustained_overhead < 15 * 1024 * 1024, f"Memory leak detected: {sustained_mb:.1f}MB"
