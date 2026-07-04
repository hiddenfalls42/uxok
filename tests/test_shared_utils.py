import asyncio
import logging

import pytest
from hypothesis import given
from hypothesis import strategies as st

from uxok.core._shared_utils import (
    drain_plugin_resources,
    format_capability_error,
    format_plugin_error,
    log_op,
    log_plugin_op,
    resolve_plugin,
)
from uxok.utils import (
    AsyncTaskManager,
    locked,
    log_context,
    safe_str,
    sanitize_identifier,
    topo_sort,
    validate_enum_value,
    validate_identifier,
    validate_positive_number,
)


@pytest.mark.asyncio
async def test_locked_releases_on_exception():
    lock = asyncio.Lock()

    async def use_lock():
        async with locked(lock):
            assert lock.locked()
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await use_lock()
    assert not lock.locked()


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
    assert sanitize_identifier("foo bar", "field") == "foo_bar"
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


@pytest.mark.asyncio
async def test_async_task_manager_cleanup_task():
    manager = AsyncTaskManager()

    async def quick_task():
        return "done"

    task = await manager.create_task(quick_task(), name="quick")
    await task  # Wait for completion
    await manager.cleanup_task(task)
    assert task not in manager._tasks


@pytest.mark.asyncio
async def test_async_task_manager_cleanup_cancelled_task():
    manager = AsyncTaskManager()
    task = await manager.create_task(asyncio.sleep(100), name="slow")
    task.cancel()
    await manager.cleanup_task(task)
    assert task not in manager._tasks


@pytest.mark.parametrize("value", [0, -1, -1.0, float("inf"), float("nan")])
def test_validate_positive_number_rejects_bad_values(value):
    with pytest.raises(ValueError):
        validate_positive_number(value, "test")


def test_validate_positive_number_accepts_good_values():
    validate_positive_number(1, "test")
    validate_positive_number(0.5, "test")
    validate_positive_number(1000, "test")


def test_sanitize_identifier_strips_and_cleans():
    assert sanitize_identifier("  hello-world  ", "field") == "hello-world"
    assert sanitize_identifier("has spaces!", "field") == "has_spaces_"


def test_sanitize_identifier_rejects_empty():
    with pytest.raises(ValueError):
        sanitize_identifier("   ", "field")


def test_sanitize_identifier_special_chars_become_underscores():
    assert sanitize_identifier("!@#$", "field") == "____"


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


def test_log_plugin_op():
    class Meta:
        id = "123"
        name = "test_plugin"
        version = "1.0.0"

    class Plugin:
        metadata = Meta()

    result = log_plugin_op("register", Plugin(), extra_key="val")
    assert result["operation"] == "register"
    assert result["plugin_id"] == "123"
    assert result["plugin_name"] == "test_plugin"
    assert result["plugin_version"] == "1.0.0"
    assert result["extra_key"] == "val"


def test_format_capability_error():
    msg = format_capability_error("storage", ["cache", "db"])
    assert "storage" in msg
    assert "cache" in msg
    assert "db" in msg


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


def test_topo_sort_empty_nodes():
    assert topo_sort([], {}) == ([], set())


def test_topo_sort_linear_chain():
    deps = {"a": {"b"}, "b": {"c"}, "c": set()}
    ordered, unresolved = topo_sort(["a", "b", "c"], deps)
    assert unresolved == set()
    assert ordered.index("c") < ordered.index("b") < ordered.index("a")


def test_topo_sort_diamond_graph_is_valid_order():
    deps = {
        "storage": set(),
        "index": {"storage"},
        "search": {"storage", "index"},
    }
    nodes = ["storage", "index", "search"]
    ordered, unresolved = topo_sort(nodes, deps)
    assert unresolved == set()
    assert set(ordered) == set(nodes)
    for node, node_deps in deps.items():
        for dep in node_deps:
            assert ordered.index(dep) < ordered.index(node)


def test_topo_sort_cycle_reports_unresolved_and_excludes_them():
    deps = {"a": {"b"}, "b": {"a"}, "c": set()}
    ordered, unresolved = topo_sort(["a", "b", "c"], deps)
    assert unresolved == {"a", "b"}
    assert ordered == ["c"]


def test_topo_sort_ignores_deps_outside_node_set():
    deps = {"a": {"missing"}}
    ordered, unresolved = topo_sort(["a"], deps)
    assert unresolved == set()
    assert ordered == ["a"]


def test_topo_sort_deterministic():
    deps = {"a": {"b", "c"}, "b": set(), "c": set(), "d": {"a"}}
    nodes = {"a", "b", "c", "d"}
    first, _ = topo_sort(nodes, deps)
    second, _ = topo_sort(nodes, deps)
    assert first == second


def test_topo_sort_independent_nodes_preserve_input_order():
    """Unconstrained nodes come out in input order, never hash order (H-001)."""
    nodes = ["echo", "delta", "charlie", "bravo", "alpha"]
    ordered, unresolved = topo_sort(nodes, {n: set() for n in nodes})
    assert unresolved == set()
    assert ordered == nodes


def test_topo_sort_ties_break_by_input_order_under_constraints():
    """A shared dependency is placed first; its independent dependents keep input order."""
    nodes = ["root", "zulu", "yankee", "xray"]
    deps = {"zulu": {"root"}, "yankee": {"root"}, "xray": {"root"}, "root": set()}
    ordered, _ = topo_sort(nodes, deps)
    assert ordered == ["root", "zulu", "yankee", "xray"]


def test_topo_sort_output_is_pure_function_of_input_order():
    """Reordering the input reorders independent siblings predictably (not by seed)."""
    deps = {"a": {"b", "c"}, "b": set(), "c": set(), "d": {"a"}}
    forward, _ = topo_sort(["b", "c", "a", "d"], deps)
    assert forward.index("b") < forward.index("c")
    reversed_siblings, _ = topo_sort(["c", "b", "a", "d"], deps)
    assert reversed_siblings.index("c") < reversed_siblings.index("b")


@given(st.lists(st.integers(min_value=0, max_value=50), unique=True, max_size=12))
def test_topo_sort_independent_nodes_property(nodes):
    """Property: with no dependencies, output is exactly the input order, every run."""
    ordered, unresolved = topo_sort(nodes, {n: set() for n in nodes})
    assert unresolved == set()
    assert ordered == nodes


@given(st.lists(st.integers(min_value=0, max_value=50), unique=True, min_size=1, max_size=10))
def test_topo_sort_chain_is_pure_and_valid_property(nodes):
    """Property: a chain built from input order sorts to a stable, valid reverse order."""
    # Each node depends on its predecessor in the input list → a strict chain.
    deps = {node: ({nodes[i - 1]} if i else set()) for i, node in enumerate(nodes)}
    first, unresolved = topo_sort(nodes, deps)
    assert unresolved == set()
    assert first == nodes  # dependency-before-dependent already matches input order
    assert topo_sort(nodes, deps)[0] == first  # pure function: repeat calls agree
