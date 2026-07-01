"""Integration tests for hot-reload via core.load_plugin().

These exercise the real reload path on a running core: handler replacement,
zero-downtime capability access, exactly-once event delivery across a swap,
and rollback when the new version fails to start.
"""

import asyncio
from uuid import uuid4

import pytest

from uxok.protocols import Event

COUNTER_V1 = """
class Counter(Plugin):
    VERSION = 1

    def __init__(self, **kw):
        super().__init__(name="counter", provides={"counting"}, **kw)
        self.seen = []

    async def on_start(self):
        await self.core.events.subscribe("test.tick", self._on_tick, self.metadata.id)

    async def _on_tick(self, event):
        self.seen.append(("v1", event.data["n"]))

    def version(self):
        return 1
"""

COUNTER_V2 = (
    COUNTER_V1.replace("VERSION = 1", "VERSION = 2")
    .replace('"v1"', '"v2"')
    .replace("return 1", "return 2")
)

COUNTER_BROKEN = """
class Counter(Plugin):
    def __init__(self, **kw):
        super().__init__(name="counter", provides={"counting"}, **kw)
        self.seen = []

    async def on_start(self):
        raise RuntimeError("broken version")
"""


async def _drain(seconds: float = 0.05):
    """Let the tick loop process queued work."""
    await asyncio.sleep(seconds)


@pytest.mark.asyncio
async def test_reload_replaces_handler_behavior(started_core):
    """After a reload, the old version's handlers stop firing and the new
    version's handlers take over — no duplicates, no gaps."""
    core = started_core
    await core.load_plugin(COUNTER_V1)
    v1 = await core.get_plugin("counter")

    await core.events.publish(Event("test.tick", {"n": 0}))
    await _drain()
    assert v1.seen == [("v1", 0)]

    await core.load_plugin(COUNTER_V2)
    v2 = await core.get_plugin("counter")
    assert v2 is not v1
    assert v2.metadata.id == v1.metadata.id  # zero-downtime invariant

    await core.events.publish(Event("test.tick", {"n": 1}))
    await _drain()

    # v1 received nothing new; v2 received exactly one event.
    assert v1.seen == [("v1", 0)]
    assert v2.seen == [("v2", 1)]


@pytest.mark.asyncio
async def test_capability_resolvable_throughout_reload(started_core):
    """The capability stays resolvable before, during, and after reload."""
    core = started_core
    await core.load_plugin(COUNTER_V1)

    failures = []
    versions = []
    stop = asyncio.Event()

    async def hammer():
        while not stop.is_set():
            try:
                provider = await core.get_capability("counting")
                versions.append(provider.version())
            except Exception as e:  # capability gap — must not happen
                failures.append(e)
            await asyncio.sleep(0.001)

    hammer_task = asyncio.create_task(hammer())
    await _drain(0.03)
    await core.load_plugin(COUNTER_V2)
    await _drain(0.03)
    stop.set()
    await hammer_task

    assert failures == []
    assert 1 in versions and 2 in versions
    # Once v2 is observed, v1 never reappears.
    first_v2 = versions.index(2)
    assert all(v == 2 for v in versions[first_v2:])


@pytest.mark.asyncio
async def test_events_delivered_exactly_once_across_reloads(started_core):
    """Events published while reloads happen are each handled exactly once."""
    core = started_core
    await core.load_plugin(COUNTER_V1)

    total = 60
    instances = [await core.get_plugin("counter")]

    async def publisher():
        for n in range(total):
            await core.events.publish(Event("test.tick", {"n": n}))
            await asyncio.sleep(0.002)

    async def reloader():
        for code in (COUNTER_V2, COUNTER_V1, COUNTER_V2):
            await asyncio.sleep(0.02)
            await core.load_plugin(code)
            instances.append(await core.get_plugin("counter"))

    await asyncio.gather(publisher(), reloader())
    await _drain()

    seen = [n for plugin in instances for (_v, n) in plugin.seen]
    assert sorted(seen) == list(range(total)), (
        f"lost: {set(range(total)) - set(seen)}, "
        f"duplicated: {[n for n in set(seen) if seen.count(n) > 1]}"
    )


@pytest.mark.asyncio
async def test_failed_reload_keeps_old_version_serving(started_core):
    """A reload that fails on start leaves the old version fully serving:
    events still handled once, capability still resolvable."""
    core = started_core
    await core.load_plugin(COUNTER_V1)
    v1 = await core.get_plugin("counter")

    with pytest.raises(RuntimeError, match="broken version"):
        await core.load_plugin(COUNTER_BROKEN)

    # Old version still registered, still handling events exactly once.
    assert await core.get_plugin("counter") is v1
    await core.events.publish(Event("test.tick", {"n": 7}))
    await _drain()
    assert v1.seen == [("v1", 7)]

    # Capability still resolves to the old instance.
    provider = await core.get_capability("counting")
    assert provider is v1

    # And a good version can still be loaded afterwards.
    await core.load_plugin(COUNTER_V2)
    assert (await core.get_plugin("counter")).version() == 2


# ---------------------------------------------------------------------------
# @hook decorator replacement across reload
# ---------------------------------------------------------------------------

HOOK_PLUGIN_V1 = """
from uxok import hook

class HookPlugin(Plugin):
    def __init__(self, **kw):
        super().__init__(name="hooker", **kw)

    @hook("x.y")
    async def handle(self, **kw):
        return "v1-marker"
"""

HOOK_PLUGIN_V2 = """
from uxok import hook

class HookPlugin(Plugin):
    def __init__(self, **kw):
        super().__init__(name="hooker", **kw)

    @hook("x.y")
    async def handle(self, **kw):
        return "v2-marker"
"""


@pytest.mark.asyncio
async def test_hook_handler_replaced_after_reload(started_core):
    """After reload, only v2's @hook handler fires — no zombie double-execution.

    Mirrors the @event regression test style: register v1, execute hook
    (verify v1-only), reload to v2, execute hook again (verify v2-only,
    exactly one result).
    """
    core = started_core
    await core.load_plugin(HOOK_PLUGIN_V1)

    results_v1 = await core.hooks.execute("x.y")
    assert results_v1 == ["v1-marker"]

    await core.load_plugin(HOOK_PLUGIN_V2)

    results_v2 = await core.hooks.execute("x.y")
    # Exactly one execution, v2 result only.
    assert len(results_v2) == 1
    assert results_v2[0] == "v2-marker"


# ---------------------------------------------------------------------------
# Exclusive-resource swap window — rollback when v2 on_start fails to acquire
# ---------------------------------------------------------------------------

# The resource is tracked on the core instance as a lightweight shared namespace
# (test-only; plugin code accesses it via self.core._test_resource_held).
_RESOURCE_PLUGIN = """
class ResourcePlugin(Plugin):
    def __init__(self, **kw):
        super().__init__(name="resource", **kw)

    async def on_start(self):
        if getattr(self.core, "_test_resource_held", False):
            raise RuntimeError("resource already held")
        self.core._test_resource_held = True

    async def on_stop(self):
        self.core._test_resource_held = False
"""


@pytest.mark.asyncio
async def test_exclusive_resource_held_by_v1_blocks_v2_on_start(started_core):
    """When v2's on_start cannot acquire a resource held by v1, reload is rolled back.

    This pins the failure mode: the exception propagates, the rollback restores
    v1 as the registered instance, and the core continues to process events
    afterward (no wedged state).

    The key ordering guarantee exercised here: on_stop() is called only AFTER
    a successful swap, so v1 still holds the resource when v2's on_start runs.
    """
    core = started_core
    await core.load_plugin(_RESOURCE_PLUGIN)
    v1 = await core.get_plugin("resource")
    assert getattr(core, "_test_resource_held", False) is True

    # Reload with the same code: v2.on_start sees v1 still holding the resource.
    with pytest.raises(RuntimeError, match="resource already held"):
        await core.load_plugin(_RESOURCE_PLUGIN)

    # v1 is still the registered instance.
    assert await core.get_plugin("resource") is v1
    # v1 still holds the resource.
    assert getattr(core, "_test_resource_held", False) is True

    # Core is not wedged: events still dispatch.
    seen: list[Event] = []

    async def collect(ev: Event) -> None:
        seen.append(ev)

    await core.events.subscribe("test.alive", collect)
    await core.events.publish(Event("test.alive", {}))
    await _drain()
    assert len(seen) == 1


# ---------------------------------------------------------------------------
# Kernel-owned identity: the most natural plugin shape must hot-reload.
#
# The other plugins in this file declare ``__init__(self, core, **kw)``, whose
# ``**kw`` happens to swallow the reload ``id`` — which is exactly why the old
# constructor-id contract slipped through. These tests use the bare
# ``def __init__(self)`` shape (no **kw, no id) that a plugin author
# writes by default, and lock the kernel-owned-identity invariants.
# ---------------------------------------------------------------------------

# Most natural plugin shape: no **kw, no id parameter at all.
BARE_V1 = """
class Bare(Plugin):
    def __init__(self):
        super().__init__(name="bare", provides={"bare_cap"})

    def version(self):
        return 1
"""
BARE_V2 = BARE_V1.replace("return 1", "return 2")


@pytest.mark.asyncio
async def test_bare_constructor_plugin_hot_reloads(started_core):
    """A plugin with `def __init__(self)` (no **kw, no id) hot-reloads,
    and the kernel preserves its id across the swap."""
    core = started_core
    await core.load_plugin(BARE_V1)
    v1 = await core.get_plugin("bare")
    assert v1.version() == 1
    original_id = v1.metadata.id

    await core.load_plugin(BARE_V2)  # reload — would TypeError under the old contract
    v2 = await core.get_plugin("bare")

    assert v2 is not v1
    assert v2.version() == 2
    assert v2.metadata.id == original_id  # identity preserved across reload


@pytest.mark.asyncio
async def test_kernel_assigns_unique_ids(started_core):
    """Every plugin gets a distinct kernel-generated id; authors don't set it."""
    core = started_core
    await core.load_plugin(BARE_V1)
    bare_id = (await core.get_plugin("bare")).metadata.id

    await core.load_plugin(COUNTER_V1)
    counter_id = (await core.get_plugin("counter")).metadata.id

    assert bare_id != counter_id


def test_plugin_constructor_rejects_id(started_core):
    """`id` is retired from the public constructor — passing it is an unknown
    kwarg (TypeError), so authors cannot set identity."""
    from uxok import Plugin

    with pytest.raises(TypeError):
        Plugin(name="nope", id=uuid4())
