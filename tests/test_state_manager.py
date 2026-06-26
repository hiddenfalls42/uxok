from __future__ import annotations

from uuid import uuid4

import pytest

from uxok.core._state_manager import StateManager
from uxok.protocols import CoreState


class _DummyHookSystem:
    def __init__(self) -> None:
        self.calls: list[tuple[CoreState, CoreState]] = []

    async def execute(self, name: str, old: CoreState, new: CoreState) -> None:
        self.calls.append((old, new))


class _DummyEventBus:
    """StateManager no longer drives the bus; kept as a stand-in dependency."""


@pytest.mark.asyncio
async def test_stop_transitions_through_stopping() -> None:
    hooks = _DummyHookSystem()
    manager = StateManager(uuid4(), _DummyEventBus(), hooks)

    assert manager.state == CoreState.INITIALIZED

    await manager.start()
    assert manager.state == CoreState.RUNNING

    assert await manager.begin_stop() is True
    assert manager.state == CoreState.STOPPING
    await manager.finish_stop()
    assert manager.state == CoreState.STOPPED
    assert hooks.calls == [
        (CoreState.INITIALIZED, CoreState.RUNNING),
        (CoreState.RUNNING, CoreState.STOPPING),
        (CoreState.STOPPING, CoreState.STOPPED),
    ]


@pytest.mark.asyncio
async def test_stop_from_initialized_directly_stops() -> None:
    hooks = _DummyHookSystem()
    manager = StateManager(uuid4(), _DummyEventBus(), hooks)

    assert await manager.begin_stop() is False
    assert manager.state == CoreState.STOPPED
    assert hooks.calls == [(CoreState.INITIALIZED, CoreState.STOPPED)]


@pytest.mark.asyncio
async def test_begin_stop_when_already_stopped_is_noop() -> None:
    manager = StateManager(uuid4(), _DummyEventBus(), _DummyHookSystem())
    await manager.begin_stop()  # INITIALIZED -> STOPPED
    assert await manager.begin_stop() is False
    assert manager.state == CoreState.STOPPED


@pytest.mark.asyncio
async def test_teardown_failure_marks_failed() -> None:
    """The caller drives STOPPING -> FAILED when teardown raises."""
    hooks = _DummyHookSystem()
    manager = StateManager(uuid4(), _DummyEventBus(), hooks)

    await manager.start()
    assert await manager.begin_stop() is True
    await manager.fail()

    assert manager.state == CoreState.FAILED
    assert hooks.calls[-2:] == [
        (CoreState.RUNNING, CoreState.STOPPING),
        (CoreState.STOPPING, CoreState.FAILED),
    ]


@pytest.mark.asyncio
async def test_can_restart_from_failed() -> None:
    hooks = _DummyHookSystem()
    manager = StateManager(uuid4(), _DummyEventBus(), hooks)

    await manager.start()
    await manager.begin_stop()
    await manager.fail()

    await manager.start()
    assert manager.state == CoreState.RUNNING
    assert hooks.calls[-2:] == [
        (CoreState.FAILED, CoreState.INITIALIZED),
        (CoreState.INITIALIZED, CoreState.RUNNING),
    ]


@pytest.mark.asyncio
async def test_error_state_is_gone() -> None:
    """Plugin failures are events, not core states (decision #4)."""
    assert not hasattr(CoreState, "ERROR")
    assert {s.name for s in CoreState} == {
        "INITIALIZED",
        "RUNNING",
        "STOPPING",
        "STOPPED",
        "FAILED",
    }
