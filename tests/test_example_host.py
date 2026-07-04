"""Example host: the tutorial-series destination graph on kernel primitives.

This suite is the acceptance test for ``examples/example_host/``: every kernel
feature the program demonstrates is exercised here — deterministic batch boot
(``build_host`` → ``core.load_plugins``), the keep-whole-graph-or-nothing
rollback (``BatchLoadError``), cid-correlated request/reply (no sleeps in the
conversation path), typed + tag-selected capabilities, config validation
(``REQUIRED``), state handoff across hot-swap, the disk watcher, supervision
(``core.plugin_error`` → eviction), the admission probe, discovery
(``roster.report``, ``StalePluginError``), and graceful shutdown. The core
fixture runs under all three ``capability_access`` modes — including
``"sealed"``, the mode ``main()`` ships with.
"""

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from examples.example_host.host import build_host, host_configs
from examples.example_host.model import Model
from examples.example_host.shutdown import ShutdownHandler

from uxok import BatchLoadError, Core, Plugin, StalePluginError
from uxok.protocols import CoreState, Event

_EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "example_host"
_GRUMPY = _EXAMPLE_DIR / "grumpy_persona.py"

# Commit order is a deterministic function of the sources: sorted filenames,
# reordered only by the requires edges (agent after BOTH llm providers).
_EXPECTED_ORDER = (
    "model",
    "persona",
    "roster",
    "shutdown_handler",
    "supervisor",
    "terse_model",
    "watcher",
    "agent",
)


def _configs(**overrides: dict) -> dict:
    """host_configs() with per-plugin overrides merged in."""
    configs = host_configs()
    for plugin_name, fields in overrides.items():
        configs.setdefault(plugin_name, {}).update(fields)
    return configs


@pytest_asyncio.fixture(params=["open", "declared", "sealed"])
async def core(request):
    """A fresh core under each capability_access mode, with guaranteed cleanup."""
    c = Core(capability_access=request.param, plugin_configs=host_configs())
    await c.start()
    try:
        yield c
    finally:
        if c.state is CoreState.RUNNING:
            await c.stop()


async def _ask(core, text: str, cid: str, timeout: float = 2.0) -> str:
    """One correlated conversation turn: publish user.says, await agent.says.<cid>."""
    reply: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def on_reply(ev: Event) -> None:
        if not reply.done():
            reply.set_result(ev.data["text"])

    sub = await core.events.subscribe(f"agent.says.{cid}", on_reply)
    try:
        await core.events.publish(Event("user.says", {"cid": cid, "text": text}))
        return await asyncio.wait_for(reply, timeout)
    finally:
        await core.events.unsubscribe(sub)


async def _until(predicate, timeout: float = 2.0, interval: float = 0.02):
    """Poll an async predicate until truthy or the deadline passes."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        result = await predicate()
        if result:
            return result
        await asyncio.sleep(interval)
    return await predicate()


# ---------------------------------------------------------------------------
# Batch boot — deterministic order, all-or-nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_host_commits_in_deterministic_topological_order(core):
    names = await build_host(core)

    assert names == _EXPECTED_ORDER
    # Every provider is resolvable the moment build_host returns.
    assert await core.get_capability("llm") is not None
    assert await core.get_capability("shutdown_handling") is not None


@pytest.mark.asyncio
async def test_missing_required_config_fails_the_whole_batch():
    """watch_dir is REQUIRED: omitting it fails watcher's start at commit, and
    build_host's rollback policy unwinds the installed prefix — all or nothing."""
    configs = host_configs()
    del configs["watcher"]
    c = Core(plugin_configs=configs)
    await c.start()
    try:
        with pytest.raises(BatchLoadError) as excinfo:
            await build_host(c)

        assert excinfo.value.phase == "commit"
        assert excinfo.value.failed == "watcher"
        assert excinfo.value.installed == _EXPECTED_ORDER[:6]
        # build_host unwound the committed prefix before re-raising.
        assert (await c.list()).count == 0
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_strict_collision_policy_rejects_the_contested_graph_in_plan():
    """Under error_on_conflict the two llm providers are a plan-phase fault —
    nothing commits. The shipped graph deliberately relies on the default
    last_wins_with_warning policy plus tag selection."""
    c = Core(capability_collision="error_on_conflict", plugin_configs=host_configs())
    await c.start()
    try:
        with pytest.raises(BatchLoadError) as excinfo:
            await build_host(c)

        assert excinfo.value.phase == "plan"
        assert excinfo.value.installed == ()
        assert (await c.list()).count == 0
    finally:
        await c.stop()


# ---------------------------------------------------------------------------
# Correlated conversation — typed + tagged capability, config selection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_is_correlated_and_persona_counts(core):
    await build_host(core)

    assert await _ask(core, "hello there", cid="t1") == "Cheerfully #1: you said 'hello there'."
    assert await _ask(core, "and again", cid="t2") == "Cheerfully #2: you said 'and again'."


@pytest.mark.asyncio
async def test_agent_config_selects_the_terse_model():
    """model_tag is plain config: point it at the other provider's tag and the
    agent answers through terse_model — no code change anywhere."""
    c = Core(plugin_configs=_configs(agent={"model_tag": "terse"}))
    await c.start()
    try:
        await build_host(c)

        assert await _ask(c, "hi", cid="t1") == "Cheerfully #1: hi? noted."
    finally:
        await c.stop()


# ---------------------------------------------------------------------------
# Hot reload — the swap carries state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hot_swap_preserves_the_persona_count(core):
    await build_host(core)
    assert await _ask(core, "first", cid="t1") == "Cheerfully #1: you said 'first'."

    # Zero-downtime swap; get_state/restore_state carries the count across.
    await core.load_plugin(_GRUMPY.read_text(), origin=str(_GRUMPY))

    assert await _ask(core, "second", cid="t2") == "Grumpily #2: you said 'second'."


@pytest.mark.asyncio
async def test_watcher_hot_loads_new_and_changed_files(tmp_path):
    """Point the watcher at a scratch folder: a new file becomes a fresh plugin,
    an edit to it hot-swaps the plugin of the same name."""
    c = Core(
        plugin_configs=_configs(watcher={"watch_dir": str(tmp_path), "interval_seconds": 0.05})
    )
    await c.start()
    try:
        await build_host(c)

        greeter = tmp_path / "greeter.py"
        greeter.write_text(
            "from uxok import Plugin\n\n"
            "class Greeter(Plugin):\n"
            "    def __init__(self):\n"
            '        super().__init__(name="greeter", provides={"greeting"})\n'
            "    async def greet(self):\n"
            '        return "hi"\n'
        )

        async def greeter_live():
            return (await c.list()).by_name("greeter")

        assert await _until(greeter_live) is not None
        provider = await c.get_capability("greeting")
        assert await provider.greet() == "hi"

        await asyncio.sleep(0.05)  # ensure a distinct mtime
        greeter.write_text(greeter.read_text().replace('"hi"', '"hi v2"'))

        async def greets_v2():
            candidate = await c.get_capability("greeting")
            return await candidate.greet() == "hi v2"

        assert await _until(greets_v2)
    finally:
        await c.stop()


# ---------------------------------------------------------------------------
# Supervision — error signals consumed in plugin-land
# ---------------------------------------------------------------------------

_FLAKY_SRC = """
from uxok import Plugin, event

class Flaky(Plugin):
    def __init__(self):
        super().__init__(name="flaky")

    @event("chaos.boom")
    async def boom(self, ev):
        raise RuntimeError("kaboom")
"""


@pytest.mark.asyncio
async def test_supervisor_evicts_a_repeatedly_failing_plugin(core):
    await build_host(core)
    await core.load_plugin(_FLAKY_SRC, origin="tests/flaky")
    assert await core.get_plugin("flaky") is not None

    evicted = asyncio.get_running_loop().create_future()

    async def on_evicted(ev: Event) -> None:
        if not evicted.done():
            evicted.set_result(ev.data["plugin_name"])

    await core.events.subscribe("supervisor.evicted", on_evicted)

    # Three failing dispatches cross max_errors; the deferred review evicts.
    for _ in range(3):
        await core.events.publish(Event("chaos.boom", {}))
        await asyncio.sleep(0.05)

    assert await asyncio.wait_for(evicted, timeout=2.0) == "flaky"
    assert await core.get_plugin("flaky") is None


# ---------------------------------------------------------------------------
# Discovery — the probe, the roster, the stale view
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_plugin_probes_without_committing():
    """The admission probe reports structural faults as data — the graph is
    untouched either way."""
    c = Core(capability_collision="error_on_conflict")
    await c.start()
    try:
        await c.register_plugin(Model())

        class RivalModel(Plugin):
            def __init__(self):
                super().__init__(name="rival", provides={"llm"})

        verdict = await c.check_plugin(RivalModel())
        assert not verdict.ok
        assert "llm" in verdict.provides_conflicts

        class Needy(Plugin):
            def __init__(self):
                super().__init__(name="needy", requires={"nonexistent"})

        verdict = await c.check_plugin(Needy())
        assert not verdict.ok
        assert "nonexistent" in verdict.missing_requires

        # Probes committed nothing.
        assert (await c.list()).count == 1
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_roster_reports_the_live_graph(core):
    await build_host(core)

    report = await core.hooks.execute("roster.report", firstresult=True)

    assert report.startswith("8 plugins live")
    assert "llm" in report and "shutdown_handling" in report


@pytest.mark.asyncio
async def test_plugin_view_goes_stale_when_the_plugin_departs(core):
    await build_host(core)
    view = (await core.list()).by_name("persona")
    assert view is not None and view.ready

    await core.unregister_plugin("persona")

    with pytest.raises(StalePluginError):
        await view.uptime()


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_event_unblocks_wait(core):
    handler = ShutdownHandler()
    await core.register_plugin(handler)

    waiter = asyncio.create_task(handler.wait_for_shutdown())
    await asyncio.sleep(0)
    assert not waiter.done()

    # Any plugin emitting system.shutdown unblocks the host loop.
    await core.events.publish(Event("system.shutdown", {"source": "test"}))
    await asyncio.wait_for(waiter, timeout=1.0)
    assert waiter.done()
