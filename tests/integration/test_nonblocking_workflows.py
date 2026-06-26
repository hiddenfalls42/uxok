"""
Integration tests verifying non-blocking behavior under real-world concurrent load.

These tests simulate production scenarios (fabrication shop network, multiplayer
game server) where multiple plugins register, fire hooks, emit events, and access
capabilities concurrently.

Key property under test: plugin authors get non-blocking behavior by default
without having to think about the tick gate or concurrency primitives. Heavy
work goes to background tasks; hooks and events are lightweight coordination
signals that complete near-instantly.
"""

import asyncio
import time
from typing import Any

import pytest

from uxok import Plugin
from uxok.protocols import Event

# ---------------------------------------------------------------------------
# Fabrication shop plugins
# ---------------------------------------------------------------------------


class CNCMachinePlugin(Plugin):
    """CNC milling machine that processes machining jobs."""

    def __init__(self, *, machine_id: int = 0):
        super().__init__(
            name=f"cnc_machine_{machine_id}",
            provides={f"cnc_machining_{machine_id}"},
        )
        self.jobs_received: list[dict] = []
        self.jobs_completed: list[dict] = []
        self._machine_id = machine_id

    async def on_start(self):
        await self.core.events.subscribe(
            "job.assigned",
            self._handle_job,
            self.metadata.id,
        )

    async def _handle_job(self, event: Event):
        if event.data.get("machine_type") != "cnc":
            return
        self.jobs_received.append(event.data)
        await self.create_background_task(
            self._process_job(event.data),
            name=f"cnc_{self._machine_id}_job_{len(self.jobs_received)}",
        )

    async def _process_job(self, job: dict):
        await asyncio.sleep(job.get("duration", 0.01))
        self.jobs_completed.append(job)
        await self.emit("job_complete", {"job_id": job.get("part"), "machine_id": self._machine_id})


class PrinterPlugin(Plugin):
    """3D printer that processes additive manufacturing jobs."""

    def __init__(self, *, printer_id: int = 0):
        super().__init__(
            name=f"printer_{printer_id}",
            provides={f"3d_printing_{printer_id}"},
        )
        self.jobs_received: list[dict] = []
        self.jobs_completed: list[dict] = []
        self._printer_id = printer_id

    async def on_start(self):
        await self.core.events.subscribe(
            "job.assigned",
            self._handle_job,
            self.metadata.id,
        )

    async def _handle_job(self, event: Event):
        if event.data.get("machine_type") != "printer":
            return
        self.jobs_received.append(event.data)
        await self.create_background_task(
            self._process_job(event.data),
            name=f"printer_{self._printer_id}_job_{len(self.jobs_received)}",
        )

    async def _process_job(self, job: dict):
        await asyncio.sleep(job.get("duration", 0.01))
        self.jobs_completed.append(job)
        await self.emit("job_complete", {"job_id": job.get("part"), "printer_id": self._printer_id})


class LaserCutterPlugin(Plugin):
    """Laser cutter for sheet metal processing."""

    def __init__(self, *, cutter_id: int = 0):
        super().__init__(
            name=f"laser_cutter_{cutter_id}",
            provides={f"laser_cutting_{cutter_id}"},
        )
        self.jobs_received: list[dict] = []
        self.jobs_completed: list[dict] = []

    async def on_start(self):
        await self.core.events.subscribe(
            "job.assigned",
            self._handle_job,
            self.metadata.id,
        )

    async def _handle_job(self, event: Event):
        if event.data.get("machine_type") != "laser":
            return
        self.jobs_received.append(event.data)
        await self.create_background_task(
            self._process_job(event.data),
            name=f"laser_job_{len(self.jobs_received)}",
        )

    async def _process_job(self, job: dict):
        await asyncio.sleep(job.get("duration", 0.01))
        self.jobs_completed.append(job)
        await self.emit("job_complete", {"job_id": job.get("part")})


class QualityInspectorPlugin(Plugin):
    """Inspects completed parts from any machine."""

    def __init__(self):
        super().__init__(
            name="quality_inspector",
            provides={"quality_inspection"},
        )
        self.inspections: list[dict] = []

    async def on_start(self):
        await self.core.events.subscribe(
            "*",
            self._on_any_event,
            self.metadata.id,
        )

    async def _on_any_event(self, event: Event):
        if event.name == "job_complete":
            self.inspections.append(event.data)


class JobSchedulerPlugin(Plugin):
    """Central scheduler that dispatches jobs to machines."""

    def __init__(self):
        super().__init__(
            name="job_scheduler",
            provides={"job_scheduling"},
        )
        self.dispatched: list[dict] = []

    async def dispatch_job(self, job: dict):
        self.dispatched.append(job)
        event = Event(name="job.assigned", data=job)
        await self.core.events.publish(event)


# ---------------------------------------------------------------------------
# Game server plugins (for the non-blocking hooks test)
# ---------------------------------------------------------------------------


class PhysicsEnginePlugin(Plugin):
    """Simulates physics computations of varying cost."""

    def __init__(self, *, compute_time: float = 0.05):
        super().__init__(name="physics_engine", provides={"physics"})
        self.compute_time = compute_time
        self.ticks_processed: list[float] = []
        self.results_ready = asyncio.Event()
        self._pending_count = 0

    async def on_start(self):
        pass

    async def run_physics_step(self, **kwargs) -> dict:
        """Hook callback: kicks off heavy physics in a background task, returns immediately."""
        tick_data = kwargs.get("tick_data", {})
        self._pending_count += 1
        await self.create_background_task(
            self._compute(tick_data),
            name=f"physics_step_{len(self.ticks_processed)}",
        )
        return {"status": "computing", "engine": "physics"}

    async def _compute(self, tick_data: dict):
        await asyncio.sleep(self.compute_time)
        self.ticks_processed.append(time.monotonic())
        self._pending_count -= 1
        if self._pending_count <= 0:
            self.results_ready.set()
        await self.emit("physics_done", tick_data)


class AIControllerPlugin(Plugin):
    """Simulates AI pathfinding of varying cost."""

    def __init__(self, *, compute_time: float = 0.1):
        super().__init__(name="ai_controller", provides={"ai"})
        self.compute_time = compute_time
        self.decisions: list[float] = []
        self.results_ready = asyncio.Event()
        self._pending_count = 0

    async def on_start(self):
        pass

    async def compute_paths(self, **kwargs) -> dict:
        """Hook callback: kicks off AI pathfinding in background, returns immediately."""
        tick_data = kwargs.get("tick_data", {})
        self._pending_count += 1
        await self.create_background_task(
            self._pathfind(tick_data),
            name=f"ai_path_{len(self.decisions)}",
        )
        return {"status": "computing", "engine": "ai"}

    async def _pathfind(self, tick_data: dict):
        await asyncio.sleep(self.compute_time)
        self.decisions.append(time.monotonic())
        self._pending_count -= 1
        if self._pending_count <= 0:
            self.results_ready.set()
        await self.emit("ai_done", tick_data)


class NetworkSyncPlugin(Plugin):
    """Simulates network sync with variable latency."""

    def __init__(self, *, latency: float = 0.15):
        super().__init__(name="network_sync", provides={"networking"})
        self.latency = latency
        self.syncs: list[float] = []
        self.results_ready = asyncio.Event()
        self._pending_count = 0

    async def on_start(self):
        pass

    async def sync_state(self, **kwargs) -> dict:
        """Hook callback: kicks off network sync in background, returns immediately."""
        tick_data = kwargs.get("tick_data", {})
        self._pending_count += 1
        await self.create_background_task(
            self._sync(tick_data),
            name=f"net_sync_{len(self.syncs)}",
        )
        return {"status": "syncing", "engine": "network"}

    async def _sync(self, tick_data: dict):
        await asyncio.sleep(self.latency)
        self.syncs.append(time.monotonic())
        self._pending_count -= 1
        if self._pending_count <= 0:
            self.results_ready.set()
        await self.emit("sync_done", tick_data)


class RenderPlugin(Plugin):
    """Simulates a renderer with trivial hook cost but heavy background work."""

    def __init__(self, *, render_time: float = 0.2):
        super().__init__(name="renderer", provides={"rendering"})
        self.render_time = render_time
        self.frames: list[float] = []
        self.results_ready = asyncio.Event()
        self._pending_count = 0

    async def on_start(self):
        pass

    async def render_frame(self, **kwargs) -> dict:
        """Hook callback: kicks off render in background, returns immediately."""
        tick_data = kwargs.get("tick_data", {})
        self._pending_count += 1
        await self.create_background_task(
            self._render(tick_data),
            name=f"render_{len(self.frames)}",
        )
        return {"status": "rendering", "engine": "render"}

    async def _render(self, tick_data: dict):
        await asyncio.sleep(self.render_time)
        self.frames.append(time.monotonic())
        self._pending_count -= 1
        if self._pending_count <= 0:
            self.results_ready.set()
        await self.emit("render_done", tick_data)


# ===========================================================================
# TEST 1: Fabrication shop — concurrent mixed operations
# ===========================================================================


@pytest.mark.asyncio
async def test_fabrication_shop_concurrent_operations(started_core):
    """
    Simulates a fabrication shop with multiple machines, a job scheduler,
    and a quality inspector all operating concurrently.

    Registers plugins concurrently, dispatches jobs across machine types,
    fires validation hooks, and queries capabilities — all in parallel.
    Verifies no operations are lost and the system remains consistent.
    """
    core = started_core

    cnc_0 = CNCMachinePlugin(machine_id=0)
    cnc_1 = CNCMachinePlugin(machine_id=1)
    printer_0 = PrinterPlugin(printer_id=0)
    printer_1 = PrinterPlugin(printer_id=1)
    laser = LaserCutterPlugin(cutter_id=0)
    inspector = QualityInspectorPlugin()
    scheduler = JobSchedulerPlugin()

    all_plugins = [cnc_0, cnc_1, printer_0, printer_1, laser, inspector, scheduler]

    # Phase 1: Register all plugins concurrently
    registrations = await asyncio.gather(*[core.register_plugin(p) for p in all_plugins])
    assert all(registrations), "All plugins should register successfully"

    # Phase 2: Register validation hooks
    validation_calls: list[dict] = []

    async def validate_job(**kwargs):
        validation_calls.append(kwargs)
        return {"valid": True}

    async def log_job(**kwargs):
        pass

    await core.hooks.register("validate_job", validate_job, priority=10)
    await core.hooks.register("validate_job", log_job, priority=5)

    # Phase 3: Concurrent operations — dispatch jobs, execute hooks,
    # query capabilities, and register a late-arriving plugin all at once

    cnc_jobs = [{"machine_type": "cnc", "part": f"gear_{i}", "duration": 0.02} for i in range(5)]
    printer_jobs = [
        {"machine_type": "printer", "part": f"bracket_{i}", "duration": 0.02} for i in range(5)
    ]
    laser_jobs = [
        {"machine_type": "laser", "part": f"panel_{i}", "duration": 0.02} for i in range(3)
    ]
    all_jobs = cnc_jobs + printer_jobs + laser_jobs

    async def dispatch_all_jobs():
        await asyncio.gather(*[scheduler.dispatch_job(job) for job in all_jobs])

    async def execute_validation_hooks():
        results = []
        for job in all_jobs:
            r = await core._hook_system.execute("validate_job", job_data=job)
            results.append(r)
        return results

    async def query_all_capabilities():
        cap_names = [
            "cnc_machining_0",
            "cnc_machining_1",
            "3d_printing_0",
            "3d_printing_1",
            "laser_cutting_0",
            "quality_inspection",
            "job_scheduling",
        ]
        results = {}
        for cap in cap_names:
            provider = (await core.list()).capability.provides(cap).first()
            results[cap] = provider is not None
        return results

    async def register_late_plugin():
        late_laser = LaserCutterPlugin(cutter_id=1)
        await core.register_plugin(late_laser)
        return late_laser

    _, hook_results, cap_results, late_laser = await asyncio.gather(
        dispatch_all_jobs(),
        execute_validation_hooks(),
        query_all_capabilities(),
        register_late_plugin(),
    )

    # Wait for background jobs to complete
    await asyncio.sleep(0.5)

    # Verify hooks executed for every job (2 hooks per job name)
    assert len(validation_calls) == len(all_jobs), (
        f"Expected {len(all_jobs)} validation hook calls, got {len(validation_calls)}"
    )

    # Verify all original capabilities are resolvable
    for cap_name, found in cap_results.items():
        assert found, f"Capability '{cap_name}' should be resolvable"

    # Verify the late plugin's capability is also available
    late_cap = (await core.list()).capability.provides("laser_cutting_1").first()
    assert late_cap is not None, "Late-registered laser cutter should be available"

    # Verify CNC jobs received (each machine gets all cnc events)
    total_cnc_received = len(cnc_0.jobs_received) + len(cnc_1.jobs_received)
    assert total_cnc_received == 10, (
        f"Both CNC machines should each receive 5 CNC job events (total 10), got {total_cnc_received}"
    )

    # Verify printer jobs received
    total_printer_received = len(printer_0.jobs_received) + len(printer_1.jobs_received)
    assert total_printer_received == 10, (
        f"Both printers should each receive 5 printer job events (total 10), got {total_printer_received}"
    )

    # Verify CNC jobs completed via background tasks
    total_cnc_completed = len(cnc_0.jobs_completed) + len(cnc_1.jobs_completed)
    assert total_cnc_completed == 10, f"All CNC jobs should complete, got {total_cnc_completed}"

    # Verify printer jobs completed
    total_printer_completed = len(printer_0.jobs_completed) + len(printer_1.jobs_completed)
    assert total_printer_completed == 10, (
        f"All printer jobs should complete, got {total_printer_completed}"
    )

    # Verify laser jobs completed
    assert len(laser.jobs_completed) == 3, (
        f"Laser should complete 3 jobs, got {len(laser.jobs_completed)}"
    )

    # Verify quality inspector saw completions
    assert len(inspector.inspections) >= 10, (
        f"Inspector should see at least 10 completed jobs, saw {len(inspector.inspections)}"
    )

    # Verify scheduler tracked all dispatches
    assert len(scheduler.dispatched) == len(all_jobs), (
        f"Scheduler should track {len(all_jobs)} dispatches, got {len(scheduler.dispatched)}"
    )

    assert core.state.name == "RUNNING"


# ===========================================================================
# TEST 2: Non-blocking hooks with different processing times
# ===========================================================================


@pytest.mark.asyncio
async def test_hooks_with_different_processing_times_are_nonblocking(started_core):
    """
    Registers hooks with wildly different processing costs (50ms to 200ms).
    Each hook follows the intended pattern: kick off heavy work in a background
    task and return immediately. Verifies that:

    1. All hook calls return near-instantly (not blocked by background work)
    2. The total hook invocation time is orders of magnitude less than the
       sum of background processing times
    3. All background work completes independently
    4. Completion events fire for every subsystem
    """
    core = started_core

    physics = PhysicsEnginePlugin(compute_time=0.05)
    ai = AIControllerPlugin(compute_time=0.10)
    network = NetworkSyncPlugin(latency=0.15)
    renderer = RenderPlugin(render_time=0.20)

    await asyncio.gather(
        core.register_plugin(physics),
        core.register_plugin(ai),
        core.register_plugin(network),
        core.register_plugin(renderer),
    )

    # Register hooks for the game tick
    await core.hooks.register("game_tick", physics.run_physics_step, priority=100)
    await core.hooks.register("game_tick", ai.compute_paths, priority=90)
    await core.hooks.register("game_tick", network.sync_state, priority=80)
    await core.hooks.register("game_tick", renderer.render_frame, priority=70)

    # Track completion events
    completion_events: list[Event] = []

    async def track_completions(event: Event):
        if event.name.endswith("_done"):
            completion_events.append(event)

    await core.events.subscribe("*", track_completions, physics.metadata.id)

    # Fire the game_tick hook and measure how long the hook calls take
    tick_data = {"tick": 1, "dt": 0.016}

    t0 = time.monotonic()
    results = await core._hook_system.execute("game_tick", tick_data=tick_data)
    hook_wall_time = time.monotonic() - t0

    # The sum of all background work would be 0.05+0.10+0.15+0.20 = 0.50s.
    # The hook calls themselves should return in a tiny fraction of that.
    total_background_time = 0.05 + 0.10 + 0.15 + 0.20
    assert hook_wall_time < total_background_time * 0.5, (
        f"Hook invocation took {hook_wall_time:.3f}s — should be much less than "
        f"the {total_background_time:.2f}s of background work"
    )

    # All four hooks should have returned results
    assert len(results) == 4, f"Expected 4 hook results, got {len(results)}"
    statuses = {r["engine"] for r in results if isinstance(r, dict)}
    assert statuses == {"physics", "ai", "network", "render"}

    # Wait for all background tasks to complete
    await asyncio.gather(
        asyncio.wait_for(physics.results_ready.wait(), timeout=2.0),
        asyncio.wait_for(ai.results_ready.wait(), timeout=2.0),
        asyncio.wait_for(network.results_ready.wait(), timeout=2.0),
        asyncio.wait_for(renderer.results_ready.wait(), timeout=2.0),
    )

    # Verify each subsystem completed its work
    assert len(physics.ticks_processed) == 1
    assert len(ai.decisions) == 1
    assert len(network.syncs) == 1
    assert len(renderer.frames) == 1

    # Wait a beat for completion events to propagate
    await asyncio.sleep(0.1)

    # Verify completion events fired
    done_names = {e.name for e in completion_events}
    assert "physics_done" in done_names
    assert "ai_done" in done_names
    assert "sync_done" in done_names
    assert "render_done" in done_names


@pytest.mark.asyncio
async def test_repeated_hook_ticks_remain_nonblocking(started_core):
    """
    Fires the same set of hooks repeatedly (simulating a game loop) and
    verifies that each tick's hook invocation remains fast regardless of
    accumulated background work.
    """
    core = started_core

    physics = PhysicsEnginePlugin(compute_time=0.03)
    ai = AIControllerPlugin(compute_time=0.06)
    network = NetworkSyncPlugin(latency=0.04)

    await asyncio.gather(
        core.register_plugin(physics),
        core.register_plugin(ai),
        core.register_plugin(network),
    )

    await core.hooks.register("game_tick", physics.run_physics_step, priority=100)
    await core.hooks.register("game_tick", ai.compute_paths, priority=90)
    await core.hooks.register("game_tick", network.sync_state, priority=80)

    num_ticks = 10
    hook_times: list[float] = []

    for i in range(num_ticks):
        tick_data = {"tick": i, "dt": 0.016}
        t0 = time.monotonic()
        results = await core._hook_system.execute("game_tick", tick_data=tick_data)
        elapsed = time.monotonic() - t0
        hook_times.append(elapsed)
        assert len(results) == 3, f"Tick {i}: expected 3 results, got {len(results)}"

    # Each hook invocation should be fast — background work doesn't block
    max_hook_time = max(hook_times)
    avg_hook_time = sum(hook_times) / len(hook_times)
    total_per_tick_background = 0.03 + 0.06 + 0.04  # 0.13s

    assert max_hook_time < total_per_tick_background, (
        f"Slowest hook invocation was {max_hook_time:.3f}s — "
        f"should be less than background work time {total_per_tick_background:.2f}s"
    )

    # Wait for all background work to finish
    await asyncio.sleep(0.5)

    assert len(physics.ticks_processed) == num_ticks
    assert len(ai.decisions) == num_ticks
    assert len(network.syncs) == num_ticks


# ===========================================================================
# TEST 3: Concurrent hook execution across independent hook names
# ===========================================================================


@pytest.mark.asyncio
async def test_independent_hooks_execute_concurrently(started_core):
    """
    Registers hooks under different names and executes them concurrently
    via asyncio.gather. Verifies that independent hook namespaces don't
    interfere with each other and all complete correctly.
    """
    core = started_core

    results_a: list[str] = []
    results_b: list[str] = []
    results_c: list[str] = []

    async def hook_a(**kwargs):
        results_a.append("a")
        return "result_a"

    async def hook_b(**kwargs):
        results_b.append("b")
        return "result_b"

    async def hook_c(**kwargs):
        results_c.append("c")
        return "result_c"

    await core.hooks.register("system.auth", hook_a, priority=10)
    await core.hooks.register("system.metrics", hook_b, priority=10)
    await core.hooks.register("system.logging", hook_c, priority=10)

    # Fire all three concurrently, many times
    iterations = 50

    async def fire_hook(name: str, count: int):
        for _ in range(count):
            await core._hook_system.execute(name)

    await asyncio.gather(
        fire_hook("system.auth", iterations),
        fire_hook("system.metrics", iterations),
        fire_hook("system.logging", iterations),
    )

    assert len(results_a) == iterations, (
        f"Hook A should fire {iterations} times, got {len(results_a)}"
    )
    assert len(results_b) == iterations, (
        f"Hook B should fire {iterations} times, got {len(results_b)}"
    )
    assert len(results_c) == iterations, (
        f"Hook C should fire {iterations} times, got {len(results_c)}"
    )


# ===========================================================================
# TEST 4: Full mixed concurrent stress test
# ===========================================================================


@pytest.mark.asyncio
async def test_all_operations_interleaved_under_load(started_core):
    """
    Simultaneously performs every type of framework operation:
      - Registers plugins
      - Emits events
      - Executes hooks
      - Queries capabilities
      - Hot-loads a plugin via unregister/register cycle

    Models a busy production system where all operations overlap in time.
    Verifies correctness and stability after the storm.
    """
    core = started_core

    event_log: list[str] = []

    # Pre-register some plugins
    base_plugins = []
    for i in range(5):
        p = Plugin(name=f"worker_{i}", provides={f"work_{i}"})
        await core.register_plugin(p)
        await p.start()
        base_plugins.append(p)

    # Register hooks
    hook_results: list[Any] = []

    async def on_work(**kwargs):
        hook_results.append(kwargs)
        return {"ok": True}

    async def on_validate(**kwargs):
        return {"valid": True}

    await core.hooks.register("do_work", on_work, priority=10)
    await core.hooks.register("validate", on_validate, priority=10)

    # Subscribe to events
    async def on_event(event: Event):
        event_log.append(event.name)

    await core.events.subscribe("stress.*", on_event, base_plugins[0].metadata.id)

    # Concurrent operations
    async def emit_events(count: int):
        for i in range(count):
            await core.events.publish(Event(name="stress.ping", data={"seq": i}))

    async def execute_hooks(count: int):
        for i in range(count):
            await core._hook_system.execute("do_work", seq=i)
            await core._hook_system.execute("validate", seq=i)

    async def query_capabilities_loop(count: int):
        found = 0
        for i in range(count):
            cap_name = f"work_{i % 5}"
            provider = (await core.list()).capability.provides(cap_name).first()
            if provider is not None:
                found += 1
        return found

    async def churn_plugins():
        for i in range(3):
            name = f"ephemeral_{i}"
            p = Plugin(name=name, provides={f"ephemeral_cap_{i}"})
            await core.register_plugin(p)
            await p.start()
            await core.unregister_plugin(p.metadata.id)

    num_ops = 30

    _, _, cap_found, _ = await asyncio.gather(
        emit_events(num_ops),
        execute_hooks(num_ops),
        query_capabilities_loop(num_ops),
        churn_plugins(),
    )

    await asyncio.sleep(0.2)

    # Verify events arrived
    stress_pings = [e for e in event_log if e == "stress.ping"]
    assert len(stress_pings) == num_ops, (
        f"Expected {num_ops} stress.ping events, got {len(stress_pings)}"
    )

    # Verify hooks executed
    assert len(hook_results) == num_ops, (
        f"Expected {num_ops} do_work hook calls, got {len(hook_results)}"
    )

    # Verify capability queries all resolved
    assert cap_found == num_ops, (
        f"Expected {num_ops} successful capability lookups, got {cap_found}"
    )

    # Verify base plugins still registered
    for p in base_plugins:
        found = (await core.list()).by_id(p.metadata.id)
        assert found is not None, f"Base plugin {p.metadata.name} should still exist"

    # Verify ephemeral plugins are gone
    all_plugin_names = (await core.list()).names
    for i in range(3):
        assert f"ephemeral_{i}" not in all_plugin_names, (
            f"Ephemeral plugin ephemeral_{i} should have been unregistered"
        )

    assert core.state.name == "RUNNING"
