import asyncio
import logging

import pytest

from uxok.core._shared_utils import drain_plugin_resources
from uxok.registry._resolve import resolve_plugin
from uxok.utils import (
    AsyncTaskManager,
    format_plugin_error,
    log_context,
    log_op,
    safe_str,
    validate_enum_value,
    validate_identifier,
    validate_positive_number,
)


@pytest.mark.asyncio
async def test_async_task_manager_cancel_all():
    manager = AsyncTaskManager()
    started = asyncio.Event()

    async def long_task():
        started.set()
        await asyncio.sleep(1)

    task = await manager.create_task(long_task(), name="long_task")
    await started.wait()
    await manager.cancel_all(timeout=0.01)
    assert task.cancelled()


def test_validation_helpers():
    assert validate_identifier("foo-bar", "field") == "foo-bar"
    with pytest.raises(ValueError):
        validate_identifier("   ", "field")
    with pytest.raises(ValueError):
        validate_positive_number(-1, "number")
    with pytest.raises(ValueError):
        validate_enum_value("bad", {"good"}, "enum")


def test_safe_str_and_log_context():
    class Unprintable:
        def __str__(self):
            raise RuntimeError("nope")

    assert safe_str(Unprintable()) == "<unprintable>"
    ctx = log_context(a=1, b="two")
    assert ctx == {"a": 1, "b": "two"}


@pytest.mark.asyncio
async def test_drain_plugin_resources_runs_all_steps():
    bus_calls = []
    hook_calls = []
    capability_calls = []

    class FakeBus:
        async def unsubscribe_plugin(self, pid):
            bus_calls.append(pid)

    class FakeHooks:
        async def unregister_plugin_hooks(self, pid):
            hook_calls.append(pid)

    class FakeCaps:
        async def unregister_capabilities_by_plugin(self, pid):
            capability_calls.append(pid)

    plugin_task = asyncio.create_task(asyncio.sleep(10))
    task_manager = AsyncTaskManager()
    task_manager._tasks.add(plugin_task)

    class PluginObj:
        def __init__(self, tm):
            self._task_manager = tm

    await drain_plugin_resources(
        "pid-1",
        PluginObj(task_manager),
        FakeBus(),
        FakeHooks(),
        FakeCaps(),
        logging.getLogger("test_drain_plugin_resources"),
    )

    assert bus_calls == ["pid-1"]
    assert hook_calls == ["pid-1"]
    assert capability_calls == ["pid-1"]
    assert plugin_task.cancelled()


@pytest.mark.parametrize("value", [0, -1, -1.0, float("inf"), float("nan")])
def test_validate_positive_number_rejects_bad_values(value):
    with pytest.raises(ValueError):
        validate_positive_number(value, "test")


def test_validate_positive_number_accepts_good_values():
    validate_positive_number(1, "test")
    validate_positive_number(0.5, "test")
    validate_positive_number(1000, "test")


def test_validate_identifier_bad_chars():
    with pytest.raises(ValueError, match="must contain only"):
        validate_identifier("hello world", "field")


def test_validate_enum_value_non_string():
    with pytest.raises(ValueError, match="must be a string"):
        validate_enum_value(123, {"a"}, "field")


@pytest.mark.asyncio
async def test_async_task_manager_await_all_timeout():
    """cancel_all should cancel tasks that exceed the timeout."""
    manager = AsyncTaskManager()

    async def forever():
        await asyncio.sleep(999)

    await manager.create_task(forever(), name="forever")
    await manager.cancel_all(timeout=0.01)
    assert len(manager._tasks) == 0


def test_log_op():
    result = log_op("test_op", foo="bar")
    assert result["operation"] == "test_op"
    assert result["foo"] == "bar"


def test_format_plugin_error():
    msg = format_plugin_error("plugin-1", "not found")
    assert "plugin-1" in msg
    assert "not found" in msg


@pytest.mark.asyncio
async def test_resolve_plugin_by_name():
    """resolve_plugin finds by name when given a non-UUID string."""

    class Meta:
        def __init__(self, name, pid):
            self.name = name
            self.id = pid

    class FakePlugin:
        def __init__(self, name, pid):
            self.metadata = Meta(name, pid)

    class FakeRegistry:
        async def all(self):
            return {
                "id1": FakePlugin("alpha", "id1"),
                "id2": FakePlugin("beta", "id2"),
            }

        async def get(self, pid):
            return None

    plugin, pid = await resolve_plugin("beta", FakeRegistry())
    assert plugin is not None
    assert plugin.metadata.name == "beta"
