"""Failure-path tests for Core.stop(): the STOPPING -> FAILED transition.

Exercises the except branch in Core.stop() (src/uxok/core/_core.py,
lines ~765-769): when teardown raises, stop() logs, drives
state_manager.fail() (STOPPING -> FAILED), and re-raises the original error.
Also verifies the constitutional restart path FAILED -> INITIALIZED ->
RUNNING and a subsequent clean stop.

These tests deliberately construct Core() manually instead of using the
clean_core fixture: they intentionally break stop(), and the fixture's
teardown path would interact with the injected failure. Each test restores
the patched method and recovers the core in `finally` so a failing test
cannot leak a running core (or a running tick clock).
"""

import pytest

from tests.helpers import StubPlugin
from uxok import Core
from uxok.protocols import CoreState


async def _force_clean(core: Core) -> None:
    """Best-effort recovery: bring a core in any state down to STOPPED.

    FAILED -> INITIALIZED -> RUNNING via start() is constitutional; a clean
    stop() then completes the teardown. Patched methods must be restored
    BEFORE calling this.
    """
    if core.state is CoreState.FAILED:
        await core.start()
    if core.state in (CoreState.RUNNING, CoreState.INITIALIZED):
        await core.stop()


class TestStopFailurePaths:
    """Teardown failures inside stop() must drive the core to FAILED."""

    @pytest.mark.asyncio
    async def test_drain_all_failure_drives_failed_state(self):
        """capability_system.drain_all() raising puts the core in FAILED.

        drain_all() is the last teardown step (line ~764), so this exercises
        the except branch after plugins have already been unregistered.
        """
        core = Core()
        original_drain_all = core._capability_system.drain_all

        async def boom() -> None:
            raise RuntimeError("injected drain_all failure")

        try:
            await core.start()
            plugin = StubPlugin(name="stop_failure_stub")
            assert await core.register_plugin(plugin) is True

            core._capability_system.drain_all = boom  # type: ignore[method-assign]

            with pytest.raises(RuntimeError, match="injected drain_all failure"):
                await core.stop()

            assert core.state is CoreState.FAILED

            # Teardown ran up to drain_all: the plugin graph is already empty.
            assert await core._registry.contains(plugin.metadata.id) is False

            # stop() from FAILED is a no-op (begin_stop() returns False):
            # no exception, state unchanged.
            await core.stop()
            assert core.state is CoreState.FAILED
        finally:
            core._capability_system.drain_all = original_drain_all  # type: ignore[method-assign]
            await _force_clean(core)

    @pytest.mark.asyncio
    async def test_tick_clock_stop_failure_drives_failed_state(self):
        """tick_clock.stop() raising puts the core in FAILED.

        tick_clock.stop() is the FIRST teardown step after begin_stop()
        (line ~740), so this exercises the except branch before any plugin
        unregistration or gate deactivation has happened.
        """
        core = Core()
        original_clock_stop = core._tick_clock.stop

        async def boom() -> None:
            raise RuntimeError("injected tick clock failure")

        try:
            await core.start()

            core._tick_clock.stop = boom  # type: ignore[method-assign]

            with pytest.raises(RuntimeError, match="injected tick clock failure"):
                await core.stop()

            assert core.state is CoreState.FAILED
        finally:
            # Restore first: the real clock task is still running because the
            # injected stop() raised before stopping it. _force_clean restarts
            # (FAILED -> RUNNING; TickClock.start() is idempotent) and then
            # performs a real, clean stop.
            core._tick_clock.stop = original_clock_stop  # type: ignore[method-assign]
            await _force_clean(core)

    @pytest.mark.asyncio
    async def test_failed_core_is_restartable_and_stops_cleanly(self):
        """FAILED -> INITIALIZED -> RUNNING -> STOPPING -> STOPPED.

        After a teardown failure leaves the core FAILED, start() must succeed
        (the constitutional restart edge) and a subsequent clean stop() must
        leave the core STOPPED.
        """
        core = Core()
        original_drain_all = core._capability_system.drain_all

        async def boom() -> None:
            raise RuntimeError("injected drain_all failure")

        try:
            await core.start()
            plugin = StubPlugin(name="restartability_stub")
            assert await core.register_plugin(plugin) is True

            core._capability_system.drain_all = boom  # type: ignore[method-assign]
            with pytest.raises(RuntimeError):
                await core.stop()
            assert core.state is CoreState.FAILED

            # Restore teardown and restart from FAILED.
            core._capability_system.drain_all = original_drain_all  # type: ignore[method-assign]
            await core.start()
            assert core.state is CoreState.RUNNING

            # The restarted core is fully functional: a fresh plugin graph
            # registers fine (instances are one-shot, so use a new instance).
            fresh = StubPlugin(name="post_restart_stub")
            assert await core.register_plugin(fresh) is True

            # And a clean stop now completes the constitutional graph.
            await core.stop()
            assert core.state is CoreState.STOPPED
        finally:
            core._capability_system.drain_all = original_drain_all  # type: ignore[method-assign]
            await _force_clean(core)
