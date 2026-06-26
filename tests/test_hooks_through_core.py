"""Hooks executed through a started Core with realistic handler signatures.

Regression tests for the `_tick_context` injection bug: once the tick clock is
running, the hook system must not pass `_tick_context` to handlers that don't
accept it, and hook failures must be observable via the `core.hook_error` event.
"""

import asyncio

import pytest

from tests.helpers import wait_until
from uxok import Plugin, hook
from uxok.protocols import Event


class PlainHookPlugin(Plugin):
    """Plugin whose handlers use plain signatures — no **kwargs."""

    def __init__(self):
        super().__init__(name="plain_hooks")
        self.calls = []

    @hook("data.process")
    async def process(self, data):
        self.calls.append(data)
        return {"processed": data}

    @hook("data.sync_process")
    def sync_process(self, data):
        self.calls.append(("sync", data))
        return data * 2


class KwargsHookPlugin(Plugin):
    """Plugin whose handler opts into tick context via **kwargs."""

    def __init__(self):
        super().__init__(name="kwargs_hooks")
        self.contexts = []

    @hook("data.aware")
    async def aware(self, data, **kwargs):
        self.contexts.append(kwargs.get("_tick_context"))
        return data


class ExplicitTickContextPlugin(Plugin):
    """Plugin whose handler declares _tick_context explicitly."""

    def __init__(self):
        super().__init__(name="explicit_ctx")
        self.contexts = []

    @hook("data.explicit")
    async def explicit(self, data, _tick_context=None):
        self.contexts.append(_tick_context)
        return data


class FailingHookPlugin(Plugin):
    def __init__(self):
        super().__init__(name="failing_hooks")

    @hook("data.fail")
    async def fail(self, data):
        raise RuntimeError("intentional hook failure")


async def _wait_for_ticks(core, min_tick=1, timeout=1.0):
    """Wait until the tick clock has advanced past min_tick."""

    async def _wait():
        while core.tick < min_tick:
            await asyncio.sleep(0.002)

    await asyncio.wait_for(_wait(), timeout=timeout)


class TestPlainSignatureHooks:
    @pytest.mark.asyncio
    async def test_plain_async_hook_runs_on_started_core(self, clean_core):
        """A plain-signature async hook must run and return its result."""
        core = clean_core
        plugin = PlainHookPlugin()
        await core.register_plugin(plugin)
        await _wait_for_ticks(core)

        results = await core.hooks.execute("data.process", {"x": 1})

        assert plugin.calls == [{"x": 1}]
        assert results == [{"processed": {"x": 1}}]

    @pytest.mark.asyncio
    async def test_plain_sync_hook_runs_on_started_core(self, clean_core):
        """A plain-signature sync hook must run and return its result."""
        core = clean_core
        plugin = PlainHookPlugin()
        await core.register_plugin(plugin)
        await _wait_for_ticks(core)

        results = await core.hooks.execute("data.sync_process", 21)

        assert ("sync", 21) in plugin.calls
        assert results == [42]

    @pytest.mark.asyncio
    async def test_kwargs_hook_still_receives_tick_context(self, clean_core):
        """A **kwargs handler keeps receiving _tick_context with the live tick."""
        core = clean_core
        plugin = KwargsHookPlugin()
        await core.register_plugin(plugin)
        await _wait_for_ticks(core)

        await core.hooks.execute("data.aware", "payload")

        assert len(plugin.contexts) == 1
        ctx = plugin.contexts[0]
        assert ctx is not None
        assert ctx["tick"] >= 1
        assert "slip" in ctx

    @pytest.mark.asyncio
    async def test_explicit_tick_context_param_receives_it(self, clean_core):
        """A handler that names _tick_context explicitly receives it."""
        core = clean_core
        plugin = ExplicitTickContextPlugin()
        await core.register_plugin(plugin)
        await _wait_for_ticks(core)

        await core.hooks.execute("data.explicit", "payload")

        assert len(plugin.contexts) == 1
        assert plugin.contexts[0] is not None
        assert plugin.contexts[0]["tick"] >= 1


class TestHookErrorObservability:
    @pytest.mark.asyncio
    async def test_failing_hook_emits_hook_error_event(self, clean_core):
        """A raising hook isolates to None AND publishes core.hook_error."""
        core = clean_core
        plugin = FailingHookPlugin()
        await core.register_plugin(plugin)
        await _wait_for_ticks(core)

        errors = []

        async def on_error(event: Event):
            errors.append(event)

        await core.events.subscribe("core.hook_error", on_error)

        results = await core.hooks.execute("data.fail", "payload")
        assert results == [None]

        # Error event is dispatched through the gate; allow a few ticks.
        await wait_until(lambda: len(errors) == 1)
        data = errors[0].data
        assert data["hook_name"] == "data.fail"
        assert data["error_type"] == "RuntimeError"
        assert "intentional hook failure" in data["error"]
        assert data["plugin_id"] == str(plugin.metadata.id)


class TestDecoratorRegistrationCorrectness:
    """Regression tests for decorator discovery bugs (audit H3/H4/H5)."""

    @pytest.mark.asyncio
    async def test_two_event_handlers_same_pattern_both_fire(self, clean_core):
        """Two @event methods on the same pattern must both be registered."""
        from uxok import event

        class TwoHandlers(Plugin):
            def __init__(self):
                super().__init__(name="two_handlers")
                self.a = 0
                self.b = 0

            @event("x.ping")
            async def h1(self, ev):
                self.a += 1

            @event("x.ping")
            async def h2(self, ev):
                self.b += 1

        core = clean_core
        plugin = TwoHandlers()
        await core.register_plugin(plugin)

        await core.events.publish(Event("x.ping", {}))
        await wait_until(lambda: plugin.a == 1 and plugin.b == 1)

    @pytest.mark.asyncio
    async def test_multi_hook_decorators_register_both_names(self, clean_core):
        """A method with two @hook decorators registers under both names,
        each with its own priority."""

        class MultiHook(Plugin):
            def __init__(self):
                super().__init__(name="multi_hook")
                self.calls = []

            @hook("multi.high", priority=10)
            @hook("multi.low")
            async def handler(self, data):
                self.calls.append(data)
                return data

        core = clean_core
        plugin = MultiHook()
        await core.register_plugin(plugin)
        await _wait_for_ticks(core)

        high_hooks = await core.hooks.get_hooks("multi.high")
        low_hooks = await core.hooks.get_hooks("multi.low")
        assert len(high_hooks) == 1
        assert len(low_hooks) == 1
        assert high_hooks[0][0] == 10
        assert low_hooks[0][0] == 0

        await core.hooks.execute("multi.high", "h")
        await core.hooks.execute("multi.low", "l")
        assert plugin.calls == ["h", "l"]


# ---------------------------------------------------------------------------
# Helpers shared by the new offensive tests
# ---------------------------------------------------------------------------


async def _capture_hook_errors(core) -> list:
    """Subscribe to core.hook_error and return the accumulator list.

    The subscriber is registered on the live core; callers must allow at least
    one tick after the execute call so the event can be dispatched.
    """
    errors: list = []

    from uxok.protocols import Event as _Event  # local to avoid shadowing

    async def _on_error(event: _Event) -> None:
        errors.append(event)

    await core.events.subscribe("core.hook_error", _on_error)
    return errors


# ---------------------------------------------------------------------------
# 1. Re-entrancy: hook-calling-hook through the gate
# ---------------------------------------------------------------------------


class TestReentrantHookExecution:
    """A hook handler that issues another execute() during its own tick frame.

    The atomic-frame property (documented in _gate.py and _system.py) claims
    that work submitted *from within* a tick executes inline, not on the next
    tick.  The mechanism is the _IN_TICK_FRAME ContextVar: set to True for the
    duration of each gate item, propagated via asyncio's context machinery to
    every coroutine awaited inside that frame.

    If the implementation is correct the outer call completes in a single
    asyncio.wait_for budget.  If _IN_TICK_FRAME is NOT propagated (e.g. a
    copy-context boundary strips it) the inner submit_and_wait will enqueue and
    the outer handler will block waiting for the inner future — which can only
    be resolved by the NEXT process_tick call.  With a short wait_for budget
    that surfaces as TimeoutError, which here we treat as a DEADLOCK and let
    the assertion fail loudly.
    """

    @pytest.mark.asyncio
    async def test_nested_execute_completes_without_deadlock(self, clean_core):
        """A hook that awaits core.hooks.execute('inner') must not deadlock."""
        core = clean_core

        inner_ran: list[str] = []
        outer_results: list = []

        class InnerPlugin(Plugin):
            def __init__(self):
                super().__init__(name="inner_plugin")

            @hook("reentrant.inner")
            async def inner_handler(self, payload):
                inner_ran.append(payload)
                return f"inner:{payload}"

        class OuterPlugin(Plugin):
            def __init__(self):
                super().__init__(name="outer_plugin")

            @hook("reentrant.outer")
            async def outer_handler(self, payload):
                # Nested execute from inside a tick frame.
                results = await self.core.hooks.execute("reentrant.inner", payload)
                outer_results.extend(results)
                return f"outer:{payload}"

        await core.register_plugin(InnerPlugin())
        await core.register_plugin(OuterPlugin())
        await _wait_for_ticks(core)

        # Wrap in wait_for: a deadlock surfaces as TimeoutError.  The 2-second
        # budget is generous — normal execution should complete in well under
        # one tick period.
        results = await asyncio.wait_for(
            core.hooks.execute("reentrant.outer", "ping"),
            timeout=2.0,
        )

        assert inner_ran == ["ping"], "inner hook did not run"
        assert outer_results == ["inner:ping"], "outer did not see inner result"
        assert results == ["outer:ping"], "outer hook result incorrect"

    @pytest.mark.asyncio
    async def test_nested_execute_inner_result_visible_to_outer(self, clean_core):
        """The outer handler receives the inner result, not an empty list."""
        core = clean_core

        class SumPlugin(Plugin):
            def __init__(self):
                super().__init__(name="sum_plugin")

            @hook("reentrant.add")
            async def add(self, value):
                return value + 1

        class AggregatorPlugin(Plugin):
            def __init__(self):
                super().__init__(name="agg_plugin")

            @hook("reentrant.aggregate")
            async def aggregate(self, value):
                inner = await self.core.hooks.execute("reentrant.add", value)
                return sum(r for r in inner if r is not None)

        await core.register_plugin(SumPlugin())
        await core.register_plugin(AggregatorPlugin())
        await _wait_for_ticks(core)

        results = await asyncio.wait_for(
            core.hooks.execute("reentrant.aggregate", 10),
            timeout=2.0,
        )

        assert results == [11]


# ---------------------------------------------------------------------------
# 2. Unregister-during-firing: snapshot semantics
# ---------------------------------------------------------------------------


class TestUnregisterDuringFiring:
    """A hook that unregisters a *later* hook in the same chain mid-execute.

    Expected behavior (snapshot semantics):
      - Round 1: the chain was snapped from the cache BEFORE unregister ran;
        the later hook still fires this round.
      - Round 2: the cache is now invalidated and rebuilt without the removed
        hook; the later hook does NOT fire.

    # SMELL: snapshot semantics are implicit and rely on the cache being a
    # mutable list reference that _remove_hooks replaces rather than mutates.
    # If cache_hooks() ever stores a copy, or _remove_hooks() mutates in-place,
    # round-1 behaviour changes silently.  The framework-coder should rule on
    # whether "snapshot of execute-start" is an explicit contract worth
    # documenting and unit-testing in _cache.py.
    """

    @pytest.mark.asyncio
    async def test_later_hook_fires_in_round_that_removes_it(self, clean_core):
        """The hook removed mid-chain still fires during the triggering execute."""
        core = clean_core

        call_log: list[str] = []

        class VictimPlugin(Plugin):
            def __init__(self):
                super().__init__(name="victim_plugin")

            @hook("snapshot.chain", priority=0)
            async def victim(self, _data):
                call_log.append("victim")
                return "victim"

        class RemoverPlugin(Plugin):
            """Higher priority — fires first and removes the victim's hook."""

            def __init__(self, victim_ref):
                super().__init__(name="remover_plugin")
                self._victim_ref = victim_ref

            @hook("snapshot.chain", priority=10)
            async def remover(self, _data):
                call_log.append("remover")
                await self.core.unregister_plugin(self._victim_ref.metadata.id)
                return "remover"

        victim = VictimPlugin()
        await core.register_plugin(victim)
        remover = RemoverPlugin(victim)
        await core.register_plugin(remover)
        await _wait_for_ticks(core)

        # Round 1: both should fire; victim is removed mid-chain.
        results_round1 = await core.hooks.execute("snapshot.chain", None)
        assert "remover" in call_log, "remover hook did not run"
        assert "victim" in call_log, "victim hook did not fire in the same round it was removed"
        assert set(results_round1) == {"remover", "victim"}

    @pytest.mark.asyncio
    async def test_removed_hook_absent_in_next_execute(self, clean_core):
        """After removal, the second execute does not include the removed hook."""
        core = clean_core

        call_log: list[str] = []

        class VictimPlugin(Plugin):
            def __init__(self):
                super().__init__(name="victim2_plugin")

            @hook("snapshot.chain2", priority=0)
            async def victim(self, _data):
                call_log.append("victim")
                return "victim"

        class RemoverPlugin(Plugin):
            def __init__(self, victim_ref):
                super().__init__(name="remover2_plugin")
                self._victim_ref = victim_ref

            @hook("snapshot.chain2", priority=10)
            async def remover(self, _data):
                call_log.append("remover")
                await self.core.unregister_plugin(self._victim_ref.metadata.id)
                return "remover"

        victim = VictimPlugin()
        await core.register_plugin(victim)
        remover = RemoverPlugin(victim)
        await core.register_plugin(remover)
        await _wait_for_ticks(core)

        # Round 1: clears victim.
        await core.hooks.execute("snapshot.chain2", None)
        call_log.clear()

        # Round 2: only remover should fire.
        results_round2 = await core.hooks.execute("snapshot.chain2", None)
        assert call_log == ["remover"]
        assert results_round2 == ["remover"]


# ---------------------------------------------------------------------------
# 3. Sync-hook failure emits core.hook_error
# ---------------------------------------------------------------------------


class TestSyncHookFailureObservability:
    """Sync counterpart to TestHookErrorObservability (async failing hook)."""

    @pytest.mark.asyncio
    async def test_failing_sync_hook_returns_none_and_emits_hook_error(self, clean_core):
        """A sync hook that raises must be isolated (None result) and observable."""
        core = clean_core

        class FailingSyncPlugin(Plugin):
            def __init__(self):
                super().__init__(name="failing_sync")

            @hook("sync.fail")
            def boom(self, data):  # sync, no async
                raise ValueError("sync hook failure")

        plugin = FailingSyncPlugin()
        await core.register_plugin(plugin)
        await _wait_for_ticks(core)

        errors = await _capture_hook_errors(core)

        results = await core.hooks.execute("sync.fail", "payload")
        assert results == [None], "failing sync hook must be isolated to None"

        await wait_until(lambda: len(errors) == 1)
        data = errors[0].data
        assert data["hook_name"] == "sync.fail"
        assert data["error_type"] == "ValueError"
        assert "sync hook failure" in data["error"]
        assert data["plugin_id"] == str(plugin.metadata.id)


# ---------------------------------------------------------------------------
# 4. Priority ordering and tie stability through the gate
# ---------------------------------------------------------------------------


class TestHookPriorityOrdering:
    """Hook execution order must be priority DESC, ties in registration order."""

    @pytest.mark.asyncio
    async def test_hooks_execute_in_priority_desc_order(self, clean_core):
        """Higher-priority hooks run before lower-priority hooks."""
        core = clean_core
        order: list[str] = []

        class LowPlugin(Plugin):
            def __init__(self):
                super().__init__(name="priority_low")

            @hook("order.test", priority=1)
            async def low(self, _data):
                order.append("low")

        class MidPlugin(Plugin):
            def __init__(self):
                super().__init__(name="priority_mid")

            @hook("order.test", priority=5)
            async def mid(self, _data):
                order.append("mid")

        class HighPlugin(Plugin):
            def __init__(self):
                super().__init__(name="priority_high")

            @hook("order.test", priority=10)
            async def high(self, _data):
                order.append("high")

        # Register in non-priority order to prove sorting is by priority not insertion.
        await core.register_plugin(LowPlugin())
        await core.register_plugin(HighPlugin())
        await core.register_plugin(MidPlugin())
        await _wait_for_ticks(core)

        await core.hooks.execute("order.test", None)

        assert order == ["high", "mid", "low"]

    @pytest.mark.asyncio
    async def test_tie_in_priority_preserves_registration_order(self, clean_core):
        """Hooks at the same priority execute in the order they were registered."""
        core = clean_core
        order: list[str] = []

        class FirstPlugin(Plugin):
            def __init__(self):
                super().__init__(name="tie_first")

            @hook("tie.test", priority=5)
            async def first(self, _data):
                order.append("first")

        class SecondPlugin(Plugin):
            def __init__(self):
                super().__init__(name="tie_second")

            @hook("tie.test", priority=5)
            async def second(self, _data):
                order.append("second")

        class ThirdPlugin(Plugin):
            def __init__(self):
                super().__init__(name="tie_third")

            @hook("tie.test", priority=5)
            async def third(self, _data):
                order.append("third")

        await core.register_plugin(FirstPlugin())
        await core.register_plugin(SecondPlugin())
        await core.register_plugin(ThirdPlugin())
        await _wait_for_ticks(core)

        await core.hooks.execute("tie.test", None)

        assert order == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# 5. Error isolation: one failing hook in a chain does not kill its neighbors
# ---------------------------------------------------------------------------


class TestHookChainErrorIsolation:
    """A single failing hook must not prevent the rest of the chain from running."""

    @pytest.mark.asyncio
    async def test_chain_ok_fail_ok_all_three_run(self, clean_core):
        """Chain [ok@10, fail@5, ok@0]: all three run; results are [v1, None, v2]."""
        core = clean_core
        ran: list[str] = []

        class FirstOkPlugin(Plugin):
            def __init__(self):
                super().__init__(name="chain_first_ok")

            @hook("chain.iso", priority=10)
            async def first(self, _data):
                ran.append("first")
                return "first_result"

        class MiddleFailPlugin(Plugin):
            def __init__(self):
                super().__init__(name="chain_middle_fail")

            @hook("chain.iso", priority=5)
            async def middle(self, _data):
                ran.append("middle")
                raise RuntimeError("middle kaboom")

        class LastOkPlugin(Plugin):
            def __init__(self):
                super().__init__(name="chain_last_ok")

            @hook("chain.iso", priority=0)
            async def last(self, _data):
                ran.append("last")
                return "last_result"

        await core.register_plugin(FirstOkPlugin())
        await core.register_plugin(MiddleFailPlugin())
        await core.register_plugin(LastOkPlugin())
        await _wait_for_ticks(core)

        errors = await _capture_hook_errors(core)

        results = await core.hooks.execute("chain.iso", None)

        assert ran == ["first", "middle", "last"], "not all three hooks ran"
        assert results == ["first_result", None, "last_result"]

        await wait_until(lambda: len(errors) == 1)
        assert errors[0].data["hook_name"] == "chain.iso"
        assert errors[0].data["error_type"] == "RuntimeError"

    @pytest.mark.asyncio
    async def test_chain_exactly_one_hook_error_emitted(self, clean_core):
        """Exactly one core.hook_error is emitted when one hook in a chain fails."""
        core = clean_core

        class OkPlugin(Plugin):
            def __init__(self):
                super().__init__(name="chain_ok_iso")

            @hook("chain.iso2", priority=10)
            async def ok(self, _data):
                return "ok"

        class FailPlugin(Plugin):
            def __init__(self):
                super().__init__(name="chain_fail_iso")

            @hook("chain.iso2", priority=5)
            async def fail(self, _data):
                raise KeyError("oops")

        class AnotherOkPlugin(Plugin):
            def __init__(self):
                super().__init__(name="chain_another_ok_iso")

            @hook("chain.iso2", priority=0)
            async def another_ok(self, _data):
                return "another_ok"

        await core.register_plugin(OkPlugin())
        await core.register_plugin(FailPlugin())
        await core.register_plugin(AnotherOkPlugin())
        await _wait_for_ticks(core)

        errors = await _capture_hook_errors(core)

        await core.hooks.execute("chain.iso2", None)

        await wait_until(lambda: len(errors) == 1)
        # Confirm no additional spurious errors arrive within a short window.
        await asyncio.sleep(0.02)
        assert len(errors) == 1, f"expected 1 hook_error, got {len(errors)}"
