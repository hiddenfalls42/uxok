"""Unit tests for the hook system: registration, execution, caching, error isolation.

Tests exercise _HookSystem through its public/internal API. The only stub used
is RecordingBus (an external dependency of _HookSystem). Internal components
(HookCache, sorting logic) are verified through observable behaviour, not by
poking at private state beyond what is necessary for cache assertions.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
import pytest_asyncio

from uxok.hooks._system import _HookSystem
from uxok.protocols.hooks import Hook

# =============================================================================
# Shared helpers and fixtures
# =============================================================================


class RecordingBus:
    """Minimal async event-bus stub that records every published event."""

    def __init__(self, *, raise_on_publish: bool = False) -> None:
        self.events: list[Any] = []
        self.raise_on_publish = raise_on_publish

    async def publish(self, event: Any) -> bool:
        if self.raise_on_publish:
            raise RuntimeError("bus exploded")
        self.events.append(event)
        return True


def make_recording_hook(
    *,
    name: str,
    priority: int = 0,
    returns: Any = None,
    raises: type[Exception] | None = None,
    is_async: bool = True,
    plugin_id: str = "",
) -> tuple[Hook, list]:
    """Return (Hook, calls_list) where calls_list grows on each invocation.

    The hook appends its positional args to calls_list so tests can assert
    which hooks fired and in what order without sharing mutable state.
    """
    calls: list = []

    if is_async:

        async def _async_fn(*args, **kwargs):
            calls.append(args)
            if raises is not None:
                raise raises("synthetic failure")
            return returns

        callback = _async_fn
    else:

        def _sync_fn(*args, **kwargs):
            calls.append(args)
            if raises is not None:
                raise raises("synthetic failure")
            return returns

        callback = _sync_fn

    hook = Hook(name=name, callback=callback, priority=priority, plugin_id=plugin_id)
    return hook, calls


@pytest_asyncio.fixture
async def hs() -> _HookSystem:
    """Bare hook system with no event bus."""
    return _HookSystem()


@pytest_asyncio.fixture
async def hs_bus() -> tuple[_HookSystem, RecordingBus]:
    """Hook system wired to a recording bus."""
    bus = RecordingBus()
    return _HookSystem(event_bus=bus), bus


# =============================================================================
# Registration
# =============================================================================


class TestRegistration:
    @pytest.mark.asyncio
    async def test_register_and_execute_async_hook(self, hs):
        hook, calls = make_recording_hook(name="my.hook", returns=42)
        await hs.register(hook.name, hook.func, priority=hook.priority, plugin_id=hook.plugin_id)
        result = await hs.execute("my.hook", "arg")
        assert calls == [("arg",)]
        assert result == [42]

    @pytest.mark.asyncio
    async def test_register_invalid_name_raises_value_error(self, hs):
        with pytest.raises(ValueError, match="Invalid hook name"):
            await hs.register("bad name!", lambda: None)

    @pytest.mark.asyncio
    async def test_register_non_callable_raises_value_error(self, hs):
        """Passing a non-callable as callback must be rejected at registration."""
        with pytest.raises(ValueError, match="handler must be callable"):
            await hs.register("my.hook", "not-a-function")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_execute_unregistered_name_returns_empty_list(self, hs):
        result = await hs.execute("never.registered")
        assert result == []


# =============================================================================
# Sync hook inline execution
# =============================================================================


class TestSyncHookExecution:
    @pytest.mark.asyncio
    async def test_sync_hook_executes_inline_on_caller_thread(self, hs):
        """Sync hooks run inline on the event-loop thread, NOT in a thread pool.

        Protocol docs (protocols/hooks.py:54-66): blocking work belongs in
        create_background_task(); dispatching hooks to a pool injects scheduling
        jitter and risks exhaustion.
        """
        caller_thread = threading.get_ident()
        hook_thread: list[int] = []

        def sync_fn(val: int) -> int:
            hook_thread.append(threading.get_ident())
            return val * 2

        hook = Hook(name="sync.double", callback=sync_fn)
        await hs.register(hook.name, hook.func, priority=hook.priority)
        result = await hs.execute("sync.double", 7)

        assert result == [14]
        assert hook_thread == [caller_thread], (
            "sync hook ran on a different thread — it was dispatched to an executor"
        )

    @pytest.mark.asyncio
    async def test_sync_hook_executes_before_next_await_point(self, hs):
        """Confirm inline execution: sync hook runs synchronously, not deferred."""
        order: list[str] = []

        def sync_fn() -> str:
            order.append("hook")
            return "done"

        hook = Hook(name="seq.hook", callback=sync_fn)
        await hs.register(hook.name, hook.func, priority=hook.priority)

        order.append("before")
        await hs.execute("seq.hook")
        order.append("after")

        assert order == ["before", "hook", "after"]


# =============================================================================
# Error isolation
# =============================================================================


class TestErrorIsolation:
    @pytest.mark.asyncio
    async def test_async_raising_hook_isolated_to_none_result(self, hs):
        """A raising async hook yields None, chain continues, no exception escapes."""
        h_ok1, calls_ok1 = make_recording_hook(name="fail.chain", priority=10, returns="v1")
        h_bad, _ = make_recording_hook(name="fail.chain", priority=5, raises=RuntimeError)
        h_ok2, calls_ok2 = make_recording_hook(name="fail.chain", priority=0, returns="v2")

        await hs.register(h_ok1.name, h_ok1.func, priority=h_ok1.priority)
        await hs.register(h_bad.name, h_bad.func, priority=h_bad.priority)
        await hs.register(h_ok2.name, h_ok2.func, priority=h_ok2.priority)

        results = await hs.execute("fail.chain")

        assert results == ["v1", None, "v2"], "failure must be isolated to None, not abort chain"
        assert len(calls_ok1) == 1, "ok1 must have run"
        assert len(calls_ok2) == 1, "ok2 must have run despite the middle hook raising"

    @pytest.mark.asyncio
    async def test_sync_raising_hook_isolated_to_none_result(self, hs):
        """A raising sync hook is also isolated — error isolation is not async-only."""
        h_ok1, calls_ok1 = make_recording_hook(
            name="sync.fail", priority=10, returns="a", is_async=False
        )
        h_bad, _ = make_recording_hook(
            name="sync.fail", priority=5, raises=ValueError, is_async=False
        )
        h_ok2, calls_ok2 = make_recording_hook(
            name="sync.fail", priority=0, returns="b", is_async=False
        )

        await hs.register(h_ok1.name, h_ok1.func, priority=h_ok1.priority)
        await hs.register(h_bad.name, h_bad.func, priority=h_bad.priority)
        await hs.register(h_ok2.name, h_ok2.func, priority=h_ok2.priority)

        results = await hs.execute("sync.fail")

        assert results == ["a", None, "b"]
        assert len(calls_ok1) == 1
        assert len(calls_ok2) == 1

    @pytest.mark.asyncio
    async def test_async_raising_hook_emits_exactly_one_hook_error_event(self, hs_bus):
        """A raising hook publishes exactly one core.hook_error with correct payload."""
        system, bus = hs_bus
        h_bad, _ = make_recording_hook(
            name="emit.fail", priority=0, raises=RuntimeError, plugin_id="plug-1"
        )
        await system.register(
            h_bad.name, h_bad.func, priority=h_bad.priority, plugin_id=h_bad.plugin_id
        )

        results = await system.execute("emit.fail")

        assert results == [None]
        assert len(bus.events) == 1, "exactly one core.hook_error event must be emitted"
        ev = bus.events[0]
        assert ev.name == "core.hook_error"
        assert ev.data["hook_name"] == "emit.fail"
        assert ev.data["plugin_id"] == "plug-1"
        assert ev.data["error_type"] == "RuntimeError"
        assert "synthetic failure" in ev.data["error"]

    @pytest.mark.asyncio
    async def test_sync_raising_hook_emits_exactly_one_hook_error_event(self, hs_bus):
        """Error-event emission is not gated on is_async — sync failures also emit."""
        system, bus = hs_bus
        h_bad, _ = make_recording_hook(
            name="sync.emit.fail",
            priority=0,
            raises=ValueError,
            is_async=False,
            plugin_id="sync-plug",
        )
        await system.register(
            h_bad.name, h_bad.func, priority=h_bad.priority, plugin_id=h_bad.plugin_id
        )

        results = await system.execute("sync.emit.fail")

        assert results == [None]
        assert len(bus.events) == 1
        ev = bus.events[0]
        assert ev.name == "core.hook_error"
        assert ev.data["error_type"] == "ValueError"
        assert ev.data["plugin_id"] == "sync-plug"

    @pytest.mark.asyncio
    async def test_no_event_bus_failure_does_not_raise(self, hs):
        """Without an event bus, a failing hook yields None with no exception."""
        h_bad, _ = make_recording_hook(name="no.bus", raises=RuntimeError)
        await hs.register(h_bad.name, h_bad.func, priority=h_bad.priority)

        result = await hs.execute("no.bus")
        assert result == [None]

    @pytest.mark.asyncio
    async def test_bus_publish_failure_swallowed_result_still_none(self, hs_bus):
        """If the bus itself raises during error publication, that secondary error
        must be swallowed — the hook result is still None and no exception escapes.
        (Covers _system.py:116-117.)
        """
        bus = RecordingBus(raise_on_publish=True)
        system = _HookSystem(event_bus=bus)

        h_bad, _ = make_recording_hook(name="bus.blow", raises=RuntimeError)
        await system.register(h_bad.name, h_bad.func, priority=h_bad.priority)

        result = await system.execute("bus.blow")
        assert result == [None], "secondary publish failure must not propagate"


# =============================================================================
# firstresult semantics
# =============================================================================


class TestFirstresult:
    @pytest.mark.asyncio
    async def test_firstresult_returns_highest_priority_non_none(self, hs):
        h_high, calls_high = make_recording_hook(name="fr", priority=10, returns="winner")
        h_low, calls_low = make_recording_hook(name="fr", priority=5, returns="loser")

        await hs.register(h_high.name, h_high.func, priority=h_high.priority)
        await hs.register(h_low.name, h_low.func, priority=h_low.priority)

        result = await hs.execute("fr", firstresult=True)

        assert result == "winner"
        assert len(calls_high) == 1
        assert calls_low == [], "lower-priority hook must NEVER be invoked after first result found"

    @pytest.mark.asyncio
    async def test_firstresult_skips_none_and_finds_next(self, hs):
        """Hooks returning None are skipped; first non-None wins."""
        h_none, _ = make_recording_hook(name="fr.skip", priority=10, returns=None)
        h_val, calls_val = make_recording_hook(name="fr.skip", priority=5, returns="found")
        h_last, calls_last = make_recording_hook(name="fr.skip", priority=0, returns="too-late")

        await hs.register(h_none.name, h_none.func, priority=h_none.priority)
        await hs.register(h_val.name, h_val.func, priority=h_val.priority)
        await hs.register(h_last.name, h_last.func, priority=h_last.priority)

        result = await hs.execute("fr.skip", firstresult=True)

        assert result == "found"
        assert len(calls_val) == 1
        assert calls_last == [], "hook after first non-None result must not run"

    @pytest.mark.asyncio
    async def test_firstresult_all_none_returns_none(self, hs):
        h1, _ = make_recording_hook(name="all.none", priority=5, returns=None)
        h2, _ = make_recording_hook(name="all.none", priority=0, returns=None)

        await hs.register(h1.name, h1.func, priority=h1.priority)
        await hs.register(h2.name, h2.func, priority=h2.priority)

        result = await hs.execute("all.none", firstresult=True)
        assert result is None

    @pytest.mark.asyncio
    async def test_firstresult_all_raise_returns_none(self, hs):
        """All hooks raising is treated the same as all-None for firstresult."""
        h1, _ = make_recording_hook(name="all.raise", priority=10, raises=RuntimeError)
        h2, _ = make_recording_hook(name="all.raise", priority=5, raises=RuntimeError)

        await hs.register(h1.name, h1.func, priority=h1.priority)
        await hs.register(h2.name, h2.func, priority=h2.priority)

        result = await hs.execute("all.raise", firstresult=True)
        assert result is None


# =============================================================================
# Priority and ordering
# =============================================================================


class TestPriorityOrdering:
    @pytest.mark.asyncio
    async def test_higher_priority_executes_first(self, hs):
        order: list[str] = []

        async def high():
            order.append("high")

        async def low():
            order.append("low")

        await hs.register("ord", low, priority=1)
        await hs.register("ord", high, priority=10)

        await hs.execute("ord")
        assert order == ["high", "low"]

    @pytest.mark.asyncio
    async def test_equal_priority_preserves_registration_order(self, hs):
        """Same-priority hooks must fire in FIFO registration order (stable sort)."""
        order: list[str] = []

        async def first():
            order.append("first")

        async def second():
            order.append("second")

        async def third():
            order.append("third")

        for fn in (first, second, third):
            await hs.register("stable", fn, priority=5)

        await hs.execute("stable")
        assert order == ["first", "second", "third"], (
            "equal-priority hooks must fire in registration order (stable sort broken)"
        )


# =============================================================================
# Cache behaviour
# =============================================================================


class TestCacheBehaviour:
    @pytest.mark.asyncio
    async def test_cache_populated_after_execute(self, hs):
        """After the first execute, the cache must be populated for that name."""
        h, _ = make_recording_hook(name="cache.me", priority=5, returns=1)
        await hs.register(h.name, h.func, priority=h.priority)

        assert hs._cache.get_cached_hooks("cache.me") is None, "cache cold before execute"
        await hs.execute("cache.me")
        cached = hs._cache.get_cached_hooks("cache.me")
        assert cached is not None, "cache must be populated after first execute"

    @pytest.mark.asyncio
    async def test_cache_sorted_content_matches_priority_order(self, hs):
        """Cached list must be sorted by priority descending, preserving registration order."""
        h_low, _ = make_recording_hook(name="cache.sort", priority=1, returns="l")
        h_high, _ = make_recording_hook(name="cache.sort", priority=10, returns="h")
        h_mid, _ = make_recording_hook(name="cache.sort", priority=5, returns="m")

        await hs.register(h_low.name, h_low.func, priority=h_low.priority)
        await hs.register(h_high.name, h_high.func, priority=h_high.priority)
        await hs.register(h_mid.name, h_mid.func, priority=h_mid.priority)

        await hs.execute("cache.sort")
        cached = hs._cache.get_cached_hooks("cache.sort")

        assert cached is not None
        priorities = [p for p, _ in cached]
        assert priorities == [10, 5, 1], f"expected [10,5,1] got {priorities}"
        hooks = [hook for _, hook in cached]
        assert hooks == [h_high, h_mid, h_low]

    @pytest.mark.asyncio
    async def test_cache_invalidated_after_register(self, hs):
        """Registering a new hook invalidates the cache for that name."""
        h1, _ = make_recording_hook(name="cache.inv", priority=5)
        await hs.register(h1.name, h1.func, priority=h1.priority)
        await hs.execute("cache.inv")  # populates cache

        assert hs._cache.get_cached_hooks("cache.inv") is not None

        h2, _ = make_recording_hook(name="cache.inv", priority=1)
        await hs.register(h2.name, h2.func, priority=h2.priority)  # must invalidate

        assert hs._cache.get_cached_hooks("cache.inv") is None, (
            "register must invalidate the cache for the affected name"
        )

    @pytest.mark.asyncio
    async def test_cache_invalidated_after_unregister(self, hs):
        """Unregistering a hook invalidates the cache for that name."""
        h, _ = make_recording_hook(name="cache.unreg", priority=5)
        await hs.register(h.name, h.func, priority=h.priority)
        await hs.execute("cache.unreg")  # warm cache

        assert hs._cache.get_cached_hooks("cache.unreg") is not None

        await hs.unregister("cache.unreg", h)

        assert hs._cache.get_cached_hooks("cache.unreg") is None, (
            "unregister must invalidate the cache"
        )

    @pytest.mark.asyncio
    async def test_execute_uses_cache_on_second_call(self, hs, monkeypatch):
        """The second execute must reuse the cached sorted list, not resort."""
        h, _ = make_recording_hook(name="reuse.cache", priority=5)
        await hs.register(h.name, h.func, priority=h.priority)

        sort_calls: list[int] = []
        original_sort = hs._sort_hooks

        def counting_sort(hooks):
            sort_calls.append(1)
            return original_sort(hooks)

        monkeypatch.setattr(hs, "_sort_hooks", counting_sort)

        await hs.execute("reuse.cache")
        await hs.execute("reuse.cache")

        assert sum(sort_calls) == 1, (
            f"_sort_hooks called {sum(sort_calls)} times; expected exactly 1 (cache miss only)"
        )


# =============================================================================
# precache_hooks content correctness
# =============================================================================


class TestPrecache:
    @pytest.mark.asyncio
    async def test_precache_all_populates_each_name(self, hs):
        h_a, _ = make_recording_hook(name="pre.a", priority=3)
        h_b, _ = make_recording_hook(name="pre.b", priority=7)

        await hs.register(h_a.name, h_a.func, priority=h_a.priority)
        await hs.register(h_b.name, h_b.func, priority=h_b.priority)
        await hs.precache_hooks()

        assert hs._cache.get_cached_hooks("pre.a") is not None
        assert hs._cache.get_cached_hooks("pre.b") is not None

    @pytest.mark.asyncio
    async def test_precache_specific_leaves_other_names_uncached(self, hs):
        h_a, _ = make_recording_hook(name="sel.a", priority=1)
        h_b, _ = make_recording_hook(name="sel.b", priority=1)

        await hs.register(h_a.name, h_a.func, priority=h_a.priority)
        await hs.register(h_b.name, h_b.func, priority=h_b.priority)
        await hs.precache_hooks(hook_names=["sel.a"])

        assert hs._cache.get_cached_hooks("sel.a") is not None
        assert hs._cache.get_cached_hooks("sel.b") is None, (
            "precache_hooks(['sel.a']) must not cache sel.b"
        )

    @pytest.mark.asyncio
    async def test_precache_content_matches_execute_order(self, hs):
        """Cache content from precache_hooks must be the same sorted list execute uses."""
        h_low, _ = make_recording_hook(name="pre.sort", priority=1, returns="l")
        h_high, _ = make_recording_hook(name="pre.sort", priority=10, returns="h")

        await hs.register(h_low.name, h_low.func, priority=h_low.priority)
        await hs.register(h_high.name, h_high.func, priority=h_high.priority)
        await hs.precache_hooks()

        cached = hs._cache.get_cached_hooks("pre.sort")
        assert cached is not None
        assert cached[0] == (10, h_high), "highest priority must be first in precached list"
        assert cached[1] == (1, h_low)

        # Execute must respect the same order: a cold-cache system must produce
        # the same priority ordering precache baked in above.
        fresh = _HookSystem()
        order_calls: list[str] = []

        async def high_fn():
            order_calls.append("high")
            return "h"

        async def low_fn():
            order_calls.append("low")
            return "l"

        await fresh.register("chk.order", high_fn, priority=10)
        await fresh.register("chk.order", low_fn, priority=1)
        await fresh.precache_hooks()
        await fresh.execute("chk.order")

        assert order_calls == ["high", "low"], (
            "precache must not affect execution order relative to a cold-cache execute"
        )


# =============================================================================
# get_hooks immutability
# =============================================================================


class TestGetHooksImmutability:
    @pytest.mark.asyncio
    async def test_get_hooks_returns_tuple(self, hs):
        h, _ = make_recording_hook(name="imm.hook")
        await hs.register(h.name, h.func, priority=h.priority)
        result = await hs.get_hooks("imm.hook")
        assert isinstance(result, tuple)

    @pytest.mark.asyncio
    async def test_get_hooks_snapshot_not_affected_by_later_registration(self, hs):
        """The returned tuple must be a snapshot: adding a hook later does not
        mutate the previously-returned reference."""
        h1, _ = make_recording_hook(name="snap.hook", priority=5)
        await hs.register(h1.name, h1.func, priority=h1.priority)
        snapshot = await hs.get_hooks("snap.hook")

        h2, _ = make_recording_hook(name="snap.hook", priority=3)
        await hs.register(h2.name, h2.func, priority=h2.priority)
        fresh = await hs.get_hooks("snap.hook")

        assert len(snapshot) == 1, "snapshot must be unchanged after second registration"
        assert len(fresh) == 2, "fresh get_hooks must reflect the new registration"

    @pytest.mark.asyncio
    async def test_get_hooks_empty_name(self, hs):
        result = await hs.get_hooks("never.registered")
        assert result == ()


# =============================================================================
# Unregistration
# =============================================================================


class TestUnregistration:
    @pytest.mark.asyncio
    async def test_unregister_returns_true_and_removes_hook(self, hs):
        h, _ = make_recording_hook(name="rm.hook")
        await hs.register(h.name, h.func, priority=h.priority)
        removed = await hs.unregister("rm.hook", h)
        assert removed is True
        assert await hs.get_hooks("rm.hook") == ()

    @pytest.mark.asyncio
    async def test_unregister_same_hook_twice_returns_false_second_time(self, hs):
        h, _ = make_recording_hook(name="rm2.hook")
        await hs.register(h.name, h.func, priority=h.priority)
        assert await hs.unregister("rm2.hook", h) is True
        assert await hs.unregister("rm2.hook", h) is False

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_name_returns_false(self, hs):
        h, _ = make_recording_hook(name="ghost")
        assert await hs.unregister("ghost", h) is False

    @pytest.mark.asyncio
    async def test_unregister_with_priority_removes_only_matching_priority(self, hs):
        h1, _ = make_recording_hook(name="prio.rm", priority=1)
        h2, _ = make_recording_hook(name="prio.rm", priority=2)
        await hs.register(h1.name, h1.func, priority=h1.priority)
        await hs.register(h2.name, h2.func, priority=h2.priority)

        removed = await hs.unregister("prio.rm", h1, priority=1)
        assert removed is True
        remaining = await hs.get_hooks("prio.rm")
        assert len(remaining) == 1
        assert remaining[0][0] == 2

    @pytest.mark.asyncio
    async def test_unregister_plugin_hooks_removes_all_for_plugin(self, hs):
        h_p1a, _ = make_recording_hook(name="plug.hook", priority=5, plugin_id="p1")
        h_p1b, _ = make_recording_hook(name="plug.hook", priority=3, plugin_id="p1")
        h_p2, _ = make_recording_hook(name="plug.hook", priority=1, plugin_id="p2")

        await hs.register(
            h_p1a.name, h_p1a.func, priority=h_p1a.priority, plugin_id=h_p1a.plugin_id
        )
        await hs.register(
            h_p1b.name, h_p1b.func, priority=h_p1b.priority, plugin_id=h_p1b.plugin_id
        )
        await hs.register(h_p2.name, h_p2.func, priority=h_p2.priority, plugin_id=h_p2.plugin_id)

        await hs.unregister_plugin_hooks("p1")
        remaining = await hs.get_hooks("plug.hook")
        assert len(remaining) == 1
        assert remaining[0][1].plugin_id == "p2"

    @pytest.mark.asyncio
    async def test_bound_method_unregister_same_instance_removes_hook(self, hs):
        """unregister matches bound methods by (func.__func__, func.__self__) identity.
        (Covers _system.py:272 — the bound-method comparison branch.)
        """

        class Owner:
            async def handler(self) -> str:
                return "ok"

        owner = Owner()
        await hs.register("bound.hook", owner.handler, priority=0)

        h_ref = Hook(name="bound.hook", callback=owner.handler, priority=0)
        removed = await hs.unregister("bound.hook", h_ref)
        assert removed is True, "unregister with same bound-method instance must succeed"
        assert await hs.get_hooks("bound.hook") == ()

    @pytest.mark.asyncio
    async def test_bound_method_unregister_different_instance_does_not_remove(self, hs):
        """A different instance's same method must NOT match the registered hook."""

        class Owner:
            async def handler(self) -> str:
                return "ok"

        owner1 = Owner()
        owner2 = Owner()

        await hs.register("bound2.hook", owner1.handler, priority=0)

        h_other = Hook(name="bound2.hook", callback=owner2.handler, priority=0)
        removed = await hs.unregister("bound2.hook", h_other)
        assert removed is False, "different instance's bound method must not match"
        assert len(await hs.get_hooks("bound2.hook")) == 1

    @pytest.mark.asyncio
    async def test_unregister_owner_hooks_removes_by_instance_identity(self, hs):
        """unregister_owner_hooks drains all hooks whose .func.__self__ is owner."""

        class Owner:
            async def h1(self) -> None: ...
            async def h2(self) -> None: ...

        owner = Owner()
        other = Owner()

        await hs.register("owner.a", owner.h1)
        await hs.register("owner.b", owner.h2)
        await hs.register("owner.a", other.h1)

        await hs.unregister_owner_hooks(owner)

        hooks_a = await hs.get_hooks("owner.a")
        hooks_b = await hs.get_hooks("owner.b")
        assert len(hooks_a) == 1
        assert hooks_a[0][1].func.__self__ is other
        assert hooks_b == (), "owner's hook on owner.b must be removed"

    @pytest.mark.asyncio
    async def test_unregister_with_non_hook_target_returns_false(self, hs):
        """unregister tolerates a non-Hook target (no .func attribute).

        hook_matches() falls through to `return stored_hook is target_hook`
        (line 282) when the target lacks .func.  A bare object is not the
        registered hook, so identity comparison is False → unregister returns
        False and the original hook is untouched.
        """
        h, _ = make_recording_hook(name="non.hook.target")
        await hs.register(h.name, h.func, priority=h.priority)

        removed = await hs.unregister("non.hook.target", object())

        assert removed is False
        assert len(await hs.get_hooks("non.hook.target")) == 1


# =============================================================================
# Unregister-during-firing (snapshot semantics)
# =============================================================================


class TestUnregisterDuringFiring:
    @pytest.mark.asyncio
    async def test_hook_unregistering_later_hook_still_fires_later_hook(self, hs):
        """execute() iterates an execute-start snapshot of the sorted chain.

        This pins the atomic-frame contract: a hook that unregisters a later
        hook mid-chain does NOT stop that later hook from firing in the current
        round. _execute_now captures `tuple(cached_hooks)` before the loop, so
        the unregister takes effect on the NEXT execute, not this one.
        """
        calls: list[str] = []

        h_later, calls_later = make_recording_hook(name="snap.hook", priority=0, returns="later")

        async def remover_fn() -> str:
            calls.append("remover")
            # unregister the lower-priority hook mid-chain
            await hs.unregister("snap.hook", h_later)
            return "remover"

        h_remover = Hook(name="snap.hook", callback=remover_fn, priority=10)
        await hs.register(h_remover.name, h_remover.func, priority=h_remover.priority)
        await hs.register(h_later.name, h_later.func, priority=h_later.priority)

        # warm the cache so the snapshot is the cached list
        await hs.execute("snap.hook")
        calls.clear()
        calls_later.clear()

        # The cache was invalidated by unregister above; the next execute
        # rebuilds from the remaining hooks (only h_remover now).
        # So we re-register h_later to test the mid-execution unregister path:
        await hs.register(h_later.name, h_later.func, priority=h_later.priority)

        results = await hs.execute("snap.hook")

        # h_remover fires first and unregisters h_later, but execute iterates the
        # execute-start snapshot, so h_later deterministically still fires this round.
        assert "remover" in results, "remover hook must have fired"
        assert calls_later, (
            "h_later must fire in this round because execute iterates an execute-start snapshot"
        )


# =============================================================================
# clear_cache
# =============================================================================


class TestClearCache:
    @pytest.mark.asyncio
    async def test_clear_cache_removes_all_entries(self, hs):
        h_a, _ = make_recording_hook(name="clr.a")
        h_b, _ = make_recording_hook(name="clr.b")
        await hs.register(h_a.name, h_a.func, priority=h_a.priority)
        await hs.register(h_b.name, h_b.func, priority=h_b.priority)
        await hs.precache_hooks()

        assert hs._cache.get_cached_hooks("clr.a") is not None
        assert hs._cache.get_cached_hooks("clr.b") is not None

        await hs.clear_cache()

        assert hs._cache.get_cached_hooks("clr.a") is None
        assert hs._cache.get_cached_hooks("clr.b") is None
