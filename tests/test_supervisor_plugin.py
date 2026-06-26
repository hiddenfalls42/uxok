"""SupervisorPlugin: restart-on-failure policy built purely on kernel primitives.

This suite is also the acceptance test for the kernel-architecture decision:
supervision policy must be implementable as a plain plugin, with state carried
across restarts via the get_state()/restore_state() contract.

RFC 0001 §2d: the supervisor reaches graph control through the tier-2
``kernel.lifecycle`` grant it declares in ``requires``. The whole suite runs under both
``capability_access="open"`` and ``"declared"`` (via the ``super_core`` fixture) so the
restart path is proven through the grant under the enforced mode, not just the no-op one.
"""

import asyncio

import pytest
import pytest_asyncio
from plugins.supervisor import SupervisorPlugin

from uxok import Core, Plugin, event
from uxok.protocols import Event


@pytest_asyncio.fixture(params=["open", "declared"])
async def super_core(request):
    """A fresh core under each capability_access mode, with guaranteed cleanup."""
    from uxok.protocols import CoreState

    core = Core(capability_access=request.param)
    try:
        yield core
    finally:
        if core.state is CoreState.RUNNING:
            await core.stop()


class CrashyWorker(Plugin):
    """Worker whose event handler crashes on demand; carries a counter."""

    def __init__(self):
        super().__init__(name="crashy_worker")
        self.processed = 0
        self.crash = False

    @event("work.item")
    async def handle(self, ev: Event):
        if self.crash:
            raise RuntimeError("worker crash")
        self.processed += 1

    async def get_state(self):
        return {"processed": self.processed}

    async def restore_state(self, state):
        self.processed = state.get("processed", 0)


async def _drain(seconds: float = 0.08):
    await asyncio.sleep(seconds)


@pytest.mark.asyncio
async def test_crashing_handler_triggers_restart_with_state_carry(super_core):
    core = super_core
    supervisor = SupervisorPlugin()
    await core.register_plugin(supervisor)

    worker = CrashyWorker()
    await core.register_plugin(worker)
    supervisor.watch("crashy_worker", factory=lambda: CrashyWorker(), max_failures=5)

    restarted = []

    async def on_restart(ev):
        restarted.append(ev.data)

    await core.events.subscribe("restarted", on_restart)

    # Build up state, then crash once.
    await core.events.publish(Event("work.item", {}))
    await core.events.publish(Event("work.item", {}))
    await _drain()
    assert worker.processed == 2

    worker.crash = True
    await core.events.publish(Event("work.item", {}))
    await _drain(0.15)

    # A fresh instance is serving, with the old instance's state carried.
    current = await core.get_plugin("crashy_worker")
    assert current is not worker
    assert current.processed == 2
    assert len(restarted) == 1
    assert restarted[0]["state_carried"] is True

    # And the replacement actually works.
    await core.events.publish(Event("work.item", {}))
    await _drain()
    assert current.processed == 3


@pytest.mark.asyncio
async def test_exceeding_failure_budget_gives_up(super_core):
    core = super_core
    supervisor = SupervisorPlugin()
    await core.register_plugin(supervisor)

    class AlwaysCrashes(Plugin):
        def __init__(self):
            super().__init__(name="always_crashes")

        @event("work.item")
        async def handle(self, ev):
            raise RuntimeError("permanent fault")

    await core.register_plugin(AlwaysCrashes())
    supervisor.watch("always_crashes", factory=lambda: AlwaysCrashes(), max_failures=2)

    gave_up = []

    async def on_gave_up(ev):
        gave_up.append(ev.data)

    await core.events.subscribe("gave_up", on_gave_up)

    # Crash repeatedly until the budget (2 in window) is exceeded.
    for _ in range(5):
        await core.events.publish(Event("work.item", {}))
        await _drain(0.06)

    assert len(gave_up) == 1
    assert gave_up[0]["plugin_name"] == "always_crashes"
    # Budget of 2 → exactly 2 restarts happened before giving up.
    assert gave_up[0]["restarts"] == 2

    # After giving up, no further restarts occur.
    watch = supervisor._watches["always_crashes"]
    assert watch.gave_up is True


@pytest.mark.asyncio
async def test_background_task_crash_triggers_restart(super_core):
    core = super_core
    supervisor = SupervisorPlugin()
    await core.register_plugin(supervisor)

    class TaskCrasher(Plugin):
        def __init__(self):
            super().__init__(name="task_crasher")

        async def crash_in_background(self):
            async def boom():
                raise ValueError("background boom")

            await self.create_background_task(boom())

    worker = TaskCrasher()
    await core.register_plugin(worker)
    supervisor.watch("task_crasher", factory=lambda: TaskCrasher())

    await worker.crash_in_background()
    await _drain(0.15)

    current = await core.get_plugin("task_crasher")
    assert current is not worker, "background-task crash did not trigger restart"


@pytest.mark.asyncio
async def test_unwatched_plugins_are_left_alone(super_core):
    core = super_core
    supervisor = SupervisorPlugin()
    await core.register_plugin(supervisor)

    worker = CrashyWorker()
    worker.crash = True
    await core.register_plugin(worker)
    # No watch registered.

    await core.events.publish(Event("work.item", {}))
    await _drain()

    assert await core.get_plugin("crashy_worker") is worker


@pytest.mark.asyncio
async def test_supervisor_crash_does_not_take_down_core(super_core):
    """Error isolation holds even for the supervisor itself."""
    core = super_core
    supervisor = SupervisorPlugin()
    await core.register_plugin(supervisor)

    # Sabotage the supervisor's failure handler.
    async def broken(_name):
        raise RuntimeError("supervisor bug")

    supervisor._record_failure = broken

    worker = CrashyWorker()
    worker.crash = True
    await core.register_plugin(worker)
    supervisor.watch("crashy_worker", factory=lambda: CrashyWorker())

    await core.events.publish(Event("work.item", {}))
    await _drain()

    # Core still running and serving.
    from uxok.protocols import CoreState

    assert core.state == CoreState.RUNNING
    assert await core.get_plugin("crashy_worker") is worker
