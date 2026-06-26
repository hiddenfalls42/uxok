"""
State machine definitions for property-based testing.

This module provides simplified state machine classes for testing
the uxok Framework's state management capabilities.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from hypothesis import assume
from hypothesis import strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, initialize, invariant, rule

from uxok import Core
from uxok.hooks._system import _HookSystem
from uxok.protocols import CoreState
from uxok.protocols.hooks import Hook


class CoreStateMachine(RuleBasedStateMachine):
    """State machine for testing core state transitions."""

    def __init__(self):
        super().__init__()
        self.state = CoreState.INITIALIZED
        self.state_history = []

    @initialize()
    def initialize(self):
        """Initialize the core state machine."""
        self.state = CoreState.INITIALIZED
        self.state_history = [self.state]

    @rule()
    def start(self):
        """Start the core."""
        if self.state == CoreState.INITIALIZED:
            self.state = CoreState.RUNNING
            self.state_history.append(self.state)

    @rule()
    def stop(self):
        """Stop the core."""
        if self.state == CoreState.RUNNING:
            self.state = CoreState.STOPPED
            self.state_history.append(self.state)

    @rule()
    def teardown(self):
        """Teardown the core."""
        if self.state == CoreState.STOPPED:
            pass  # Would transition to TERMINATED if that state existed


class EventBusStateMachine(RuleBasedStateMachine):
    """Stateful machine that drives a real started Core's event bus and checks
    delivery correctness after every step.

    Model:
        _model: subscription_id -> {
            "pattern":    str   — exact name OR fnmatch pattern subscribed to,
            "plugin_id":  str   — UUID string used at subscribe time,
            "counter":    list  — single-element mutable list [int]; the
                                  callback increments counter[0] on each call
                                  so we can read deliveries without shared state.
        }

    Async bridge:  per-machine event loop, exactly like HotReloadMachine.
    Tick-gate settlement:  asyncio.sleep(0.05) after every publish.
    """

    # Small, fixed pools keep the state space explorable and matches satisfiable.
    _EVENT_NAMES = ["a.b", "a.c", "b.d"]
    _PATTERNS = ["a.b", "a.c", "b.d", "a.*", "b.*", "*.*"]

    def __init__(self) -> None:
        super().__init__()
        # Async bridge: one private event loop per machine instance.
        self._loop = asyncio.new_event_loop()
        self._core: Core = Core()
        self._loop.run_until_complete(self._core.start())

        # Three distinct UUID plugin IDs so unsubscribe_plugin removes a useful batch.
        # Must be uuid.UUID objects: unsubscribe_plugin is UUID-only and compares by
        # `==` against the plugin_id stored at subscribe time, so a string would match
        # nothing. Real plugins always pass uuid.UUID (from metadata.id); we do the same.
        self._plugin_ids = [uuid.uuid4() for _ in range(3)]

        # model: sub_id -> {"pattern": str, "plugin_id": str, "counter": [int]}
        self._model: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run(self, coro):
        """Run a coroutine on the machine's private event loop."""
        return self._loop.run_until_complete(coro)

    def teardown(self) -> None:
        """Stop the core and close the loop on machine teardown."""
        try:
            if self._core.state is CoreState.RUNNING:
                self._loop.run_until_complete(self._core.stop())
        finally:
            self._loop.close()

    def _matches(self, event_name: str, pattern: str) -> bool:
        """Python-side model of SubscriptionManager's fnmatch dispatch."""
        import fnmatch as _fnmatch

        return _fnmatch.fnmatch(event_name, pattern)

    def _live_sub_count(self) -> int:
        """True subscription count from the implementation's authoritative map."""
        # _subscriptions_by_id has exactly one entry per subscribe() call, so
        # this is the canonical count. SubscriptionManager.count() agrees with
        # it in every reachable state; we read the map directly to keep this
        # invariant independent of the public counter it is meant to police.
        return len(self._core.events._subscriptions._subscriptions_by_id)

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    @initialize()
    def setup(self) -> None:
        """Machine-level initialise hook (state already set up in __init__)."""

    @rule(
        pattern=st.sampled_from(_PATTERNS),
        pid_index=st.integers(min_value=0, max_value=2),
    )
    def subscribe(self, pattern: str, pid_index: int) -> None:
        """Subscribe to a pattern on the real event bus; update model."""
        plugin_id = self._plugin_ids[pid_index]  # uuid.UUID — matches production path
        counter: list[int] = [0]

        async def callback(event) -> None:
            counter[0] += 1

        sub_id = self._run(self._core.events.subscribe(pattern, callback, plugin_id))
        self._model[sub_id] = {
            "pattern": pattern,
            "plugin_id": plugin_id,
            "counter": counter,
        }

    @rule(pattern=st.sampled_from(_PATTERNS))
    def unsubscribe_by_id(self, pattern: str) -> None:
        """Unsubscribe the first model sub whose pattern matches; update model."""
        candidates = [sid for sid, info in self._model.items() if info["pattern"] == pattern]
        assume(candidates)
        sid = candidates[0]
        self._run(self._core.events.unsubscribe(sid))
        del self._model[sid]

    @rule(pid_index=st.integers(min_value=0, max_value=2))
    def unsubscribe_plugin(self, pid_index: int) -> None:
        """Bulk-remove all subscriptions for one plugin_id; update model."""
        plugin_id = self._plugin_ids[pid_index]
        self._run(self._core.events.unsubscribe_plugin(plugin_id))
        self._model = {
            sid: info for sid, info in self._model.items() if info["plugin_id"] != plugin_id
        }

    @rule(event_name=st.sampled_from(_EVENT_NAMES))
    def publish(self, event_name: str) -> None:
        """Publish an event and assert exactly-correct deliveries per subscriber.

        For every live subscription:
        - If its pattern matches event_name  => counter must increment by exactly 1.
        - If its pattern does NOT match      => counter must not change.
        """
        from uxok.protocols import Event as OrionEvent

        # Snapshot delivery counts before publish.
        before = {sid: info["counter"][0] for sid, info in self._model.items()}

        self._run(self._core.events.publish(OrionEvent(event_name, {})))
        # Settle: let the tick loop deliver the event.
        self._run(asyncio.sleep(0.05))

        # Assert per-subscription delivery correctness.
        for sid, info in self._model.items():
            after = info["counter"][0]
            delta = after - before[sid]
            matched = self._matches(event_name, info["pattern"])
            if matched:
                assert delta == 1, (
                    f"publish({event_name!r}): sub {sid} (pattern={info['pattern']!r}) "
                    f"should have received +1 delivery but got +{delta} "
                    f"(before={before[sid]}, after={after})"
                )
            else:
                assert delta == 0, (
                    f"publish({event_name!r}): sub {sid} (pattern={info['pattern']!r}) "
                    f"should NOT have received event but got +{delta} "
                    f"(before={before[sid]}, after={after})"
                )

    # ------------------------------------------------------------------
    # Invariants  (checked after every rule step)
    # ------------------------------------------------------------------

    @invariant()
    def subscription_count_matches_model(self) -> None:
        """True subscription count must equal the model's live subscription count."""
        impl_count = self._live_sub_count()
        model_count = len(self._model)
        assert impl_count == model_count, (
            f"subscription_count_matches_model: "
            f"impl has {impl_count} subscriptions, model has {model_count}"
        )

    @invariant()
    def core_is_running(self) -> None:
        """Core must remain RUNNING throughout every step."""
        assert self._core.state is CoreState.RUNNING, (
            f"core_is_running: Core left RUNNING state: {self._core.state}"
        )

    @invariant()
    def no_spurious_deliveries_accumulated(self) -> None:
        """Cumulative delivery count per sub must be non-negative and reachable.

        A subscription can only receive deliveries for event names that match
        its pattern.  We can't enumerate all past events, but we CAN assert
        that a subscription whose pattern matches NO event in the pool has
        never received anything (it literally cannot have been triggered).
        """
        pool_events = set(self._EVENT_NAMES)
        for sid, info in self._model.items():
            pattern = info["pattern"]
            # Does this pattern match at least one event in the pool?
            reachable = any(self._matches(e, pattern) for e in pool_events)
            if not reachable:
                # This pattern can never be triggered; delivery count must stay 0.
                assert info["counter"][0] == 0, (
                    f"no_spurious_deliveries_accumulated: sub {sid} "
                    f"(pattern={pattern!r}) received {info['counter'][0]} delivery(ies) "
                    f"but its pattern matches nothing in the event pool {sorted(pool_events)}"
                )


# Small fixed pools keep the state space explorable.
_HOOK_NAMES = ["hook.alpha", "hook.beta", "hook.gamma", "hook.delta"]
_HOOK_PRIORITIES = [-10, 0, 5, 10, 20]

_st_hook_name = st.sampled_from(_HOOK_NAMES)
_st_priority = st.sampled_from(_HOOK_PRIORITIES)


class HookSystemStateMachine(RuleBasedStateMachine):
    """Stateful machine that drives a real _HookSystem and checks invariants.

    Model:
        _model: name -> list[(priority, hook_obj, marker)]
            Maintained in REGISTRATION ORDER.  marker is a unique string
            appended to _exec_log by the hook callback so execute_and_check_order
            can verify stable-sorted execution ordering.

    Async bridge: same asyncio.new_event_loop() / _run() pattern as HotReloadMachine.
    """

    def __init__(self) -> None:
        super().__init__()
        self._loop = asyncio.new_event_loop()
        self._hs: _HookSystem = _HookSystem()
        # name -> list of (priority, hook_obj, marker)  — registration order
        self._model: dict[str, list[tuple[int, Hook, str]]] = {}
        # reset before each execute_and_check_order call
        self._exec_log: list[str] = []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run(self, coro):
        """Run a coroutine on the machine's private event loop."""
        return self._loop.run_until_complete(coro)

    def _expected_execution_order(self, name: str) -> list[str]:
        """Return markers in the order execute() should invoke them.

        Python's sorted() is stable, so equal-priority entries keep their
        original insertion order.
        """
        entries = self._model.get(name, [])
        return [marker for _, _, marker in sorted(entries, key=lambda e: e[0], reverse=True)]

    def teardown(self) -> None:
        """Close the event loop on machine teardown."""
        self._loop.close()

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    @initialize()
    def setup(self) -> None:
        """Machine-level initialise hook (state already set up in __init__)."""

    @rule(name=_st_hook_name, priority=_st_priority)
    def register(self, name: str, priority: int) -> None:
        """Register a new hook and update the model."""
        marker = str(uuid.uuid4())
        exec_log = self._exec_log  # captured by reference — safe inside one test run

        async def callback() -> None:
            exec_log.append(marker)

        hook = Hook(name=name, callback=callback, priority=priority, plugin_id="test")
        self._run(self._hs.register(name, callback, priority=priority, plugin_id="test"))
        self._model.setdefault(name, []).append((priority, hook, marker))

    @rule(name=_st_hook_name)
    def unregister_one(self, name: str) -> None:
        """Unregister the first registered hook for name (if any) and update model."""
        entries = self._model.get(name, [])
        assume(entries)  # only fire when there is something to remove

        priority, hook, _marker = entries[0]
        result = self._run(self._hs.unregister(name, hook, priority=priority))
        assert result is True, (
            f"unregister_one: expected True for {name!r} priority={priority}, got {result}"
        )
        self._model[name] = entries[1:]
        if not self._model[name]:
            del self._model[name]

    @rule(name=_st_hook_name)
    def execute_and_check_order(self, name: str) -> None:
        """Execute hooks for name; assert results arrive in stable-sorted order."""
        self._exec_log.clear()
        self._run(self._hs.execute(name))
        expected = self._expected_execution_order(name)
        assert self._exec_log == expected, (
            f"execute_and_check_order: order mismatch for {name!r}\n"
            f"  expected: {expected}\n"
            f"  got:      {list(self._exec_log)}"
        )

    @rule()
    def precache(self) -> None:
        """Precache all hooks; cache content must match model's sorted order."""
        self._run(self._hs.precache_hooks())
        for name, entries in self._model.items():
            cached = self._hs._cache.get_cached_hooks(name)
            assert cached is not None, f"precache: {name!r} not in cache after precache_hooks()"
            expected = sorted(entries, key=lambda e: e[0], reverse=True)
            cached_pairs = [(p, h) for p, h in cached]
            expected_pairs = [(p, h) for p, h, _ in expected]
            assert cached_pairs == expected_pairs, (
                f"precache: cache mismatch for {name!r}\n"
                f"  expected: {expected_pairs}\n"
                f"  got:      {cached_pairs}"
            )

    @rule()
    def clear_cache(self) -> None:
        """Clear the hook cache; every modeled name must now be a cache miss."""
        self._run(self._hs.clear_cache())
        for name in self._model:
            cached = self._hs._cache.get_cached_hooks(name)
            assert cached is None, f"clear_cache: {name!r} still in cache after clear_cache()"

    # ------------------------------------------------------------------
    # Invariants
    # ------------------------------------------------------------------

    @invariant()
    def get_hooks_matches_model(self) -> None:
        """get_hooks() multiset must match model (registration order preserved)."""
        for name, entries in self._model.items():
            raw = self._run(self._hs.get_hooks(name))
            # get_hooks returns (priority, hook) tuples in insertion order
            assert list(raw) == [(p, h) for p, h, _ in entries], (
                f"get_hooks_matches_model: mismatch for {name!r}\n"
                f"  model:    {[(p, h) for p, h, _ in entries]}\n"
                f"  got:      {list(raw)}"
            )

    @invariant()
    def cache_consistent(self) -> None:
        """If the cache has an entry for a name it must equal the model's sorted order."""
        for name, entries in self._model.items():
            cached = self._hs._cache.get_cached_hooks(name)
            if cached is None:
                continue  # absent / invalidated — valid state
            expected = sorted(entries, key=lambda e: e[0], reverse=True)
            cached_pairs = [(p, h) for p, h in cached]
            expected_pairs = [(p, h) for p, h, _ in expected]
            assert cached_pairs == expected_pairs, (
                f"cache_consistent: stale cache for {name!r}\n"
                f"  expected: {expected_pairs}\n"
                f"  got:      {cached_pairs}"
            )

    @invariant()
    def no_phantom_names(self) -> None:
        """Names present in _hs._hooks must match model names with ≥1 entry."""
        live_names = set(self._hs._hooks.keys())
        model_names = {n for n, entries in self._model.items() if entries}
        assert live_names == model_names, (
            f"no_phantom_names: mismatch\n"
            f"  model (non-empty): {sorted(model_names)}\n"
            f"  hs._hooks keys:    {sorted(live_names)}"
        )


class ErrorRecoveryStateMachine(RuleBasedStateMachine):
    """State machine for testing error recovery scenarios."""

    def __init__(self):
        super().__init__()
        self.error_count = 0
        self.recovery_attempts = 0
        self.is_recovering = False

    @initialize()
    def setup(self):
        """Initialize error recovery state machine."""
        self.error_count = 0
        self.recovery_attempts = 0
        self.is_recovering = False

    @rule()
    def trigger_error(self):
        """Trigger an error condition."""
        self.error_count += 1

    @rule()
    def start_recovery(self):
        """Start error recovery process."""
        if self.error_count > 0 and not self.is_recovering:
            self.is_recovering = True

    @rule()
    def complete_recovery(self):
        """Complete error recovery process."""
        if self.is_recovering:
            self.recovery_attempts += 1
            self.is_recovering = False
            self.error_count = max(0, self.error_count - 1)


class PluginSystemStateMachine(RuleBasedStateMachine):
    """State machine for testing plugin system operations."""

    Plugins = Bundle("plugins")

    def __init__(self):
        super().__init__()
        self.plugins = {}
        self.capabilities = {}
        self.dependencies = {}

    @initialize()
    def setup(self):
        """Initialize plugin system state machine."""
        self.plugins = {}
        self.capabilities = {}
        self.dependencies = {}

    @rule(target=Plugins)
    def create_plugin(self):
        """Create a new plugin."""
        plugin_id = str(uuid.uuid4())
        plugin_name = f"plugin_{len(self.plugins)}"
        self.plugins[plugin_id] = {
            "name": plugin_name,
            "state": "created",
            "capabilities": set(),
            "dependencies": set(),
        }
        return plugin_id

    @rule(plugin_id=Plugins)
    def register_plugin(self, plugin_id):
        """Register a plugin."""
        if plugin_id in self.plugins:
            self.plugins[plugin_id]["state"] = "registered"

    @rule(plugin_id=Plugins)
    def start_plugin(self, plugin_id):
        """Start a plugin."""
        if plugin_id in self.plugins and self.plugins[plugin_id]["state"] == "registered":
            self.plugins[plugin_id]["state"] = "running"

    @rule(plugin_id=Plugins)
    def stop_plugin(self, plugin_id):
        """Stop a plugin."""
        if plugin_id in self.plugins and self.plugins[plugin_id]["state"] == "running":
            self.plugins[plugin_id]["state"] = "stopped"


class PluginLifecycleStateMachine(RuleBasedStateMachine):
    """State machine for testing plugin lifecycle operations."""

    def __init__(self):
        super().__init__()
        self.plugins = {}
        self.lifecycle_events = []

    @initialize()
    def setup(self):
        """Initialize plugin lifecycle state machine."""
        self.plugins = {}
        self.lifecycle_events = []

    @rule()
    def create_plugin(self):
        """Create a new plugin instance."""
        plugin_id = str(uuid.uuid4())
        plugin_name = f"lifecycle_plugin_{len(self.plugins)}"

        self.plugins[plugin_id] = {
            "name": plugin_name,
            "state": "created",
            "metadata": {
                "id": plugin_id,
                "name": plugin_name,
                "version": "1.0.0",
                "provides": set(),
                "requires": set(),
            },
        }
        self.lifecycle_events.append(("created", plugin_id))

    @rule()
    def register_plugin(self):
        """Register a plugin with the core system."""
        # Find a created plugin
        created_plugins = [pid for pid, info in self.plugins.items() if info["state"] == "created"]
        if created_plugins:
            plugin_id = created_plugins[0]
            self.plugins[plugin_id]["state"] = "registered"
            self.lifecycle_events.append(("registered", plugin_id))

    @rule()
    def start_plugin(self):
        """Start a registered plugin."""
        registered_plugins = [
            pid for pid, info in self.plugins.items() if info["state"] == "registered"
        ]
        if registered_plugins:
            plugin_id = registered_plugins[0]
            self.plugins[plugin_id]["state"] = "running"
            self.lifecycle_events.append(("started", plugin_id))

    @rule()
    def stop_plugin(self):
        """Stop a running plugin."""
        running_plugins = [pid for pid, info in self.plugins.items() if info["state"] == "running"]
        if running_plugins:
            plugin_id = running_plugins[0]
            self.plugins[plugin_id]["state"] = "stopped"
            self.lifecycle_events.append(("stopped", plugin_id))

    @rule()
    def unregister_plugin(self):
        """Unregister a stopped plugin."""
        stopped_plugins = [pid for pid, info in self.plugins.items() if info["state"] == "stopped"]
        if stopped_plugins:
            plugin_id = stopped_plugins[0]
            self.plugins[plugin_id]["state"] = "unregistered"
            self.lifecycle_events.append(("unregistered", plugin_id))

    def get_lifecycle_state(self, plugin_id: str) -> str:
        """Get current lifecycle state of a plugin."""
        return self.plugins.get(plugin_id, {}).get("state", "unknown")

    def get_event_history(self) -> list[tuple[str, str]]:
        """Get the history of lifecycle events."""
        return self.lifecycle_events.copy()


# ---------------------------------------------------------------------------
# HotReloadMachine
#
# Drives a REAL started Core through randomised hot-reload sequences and
# checks system invariants after every step.
# ---------------------------------------------------------------------------

# Small, fixed pools keep `requires` satisfiable without a constraint solver.
_PLUGIN_NAMES = ["alpha", "beta", "gamma", "delta"]
_CAP_POOL = ["cap_a", "cap_b", "cap_c", "cap_d", "cap_e"]

# Hypothesis strategies drawn inline inside rules.
_st_name = st.sampled_from(_PLUGIN_NAMES)
_st_caps = st.sets(st.sampled_from(_CAP_POOL), min_size=0, max_size=2)
_st_generation = st.integers(min_value=2, max_value=99)


def _make_plugin_code(
    name: str,
    generation: int,
    provides: set[str],
    requires: set[str],
    subscribe_probe: bool,
) -> str:
    """Return a self-contained load_plugin code string for one plugin variant.

    The generated class:
    - Stores on_stop_calls (int) and increments it in on_stop.
    - Stores delivery_count (int) and increments it on each probe.ping event
      (when subscribe_probe is True).
    - Stores GENERATION as a class attribute.
    - Implements get_state/restore_state (no-op; generation is a class attr).
    """
    class_name = name.capitalize() + "Plugin"
    lines: list[str] = []

    lines.append(f"class {class_name}(Plugin):")
    lines.append(f"    GENERATION = {generation}")
    lines.append("")
    lines.append("    def __init__(self, **kw):")
    lines.append("        super().__init__(")
    lines.append(f"            name={name!r},")
    lines.append(f"            provides={provides!r},")
    lines.append(f"            requires={requires!r},")
    lines.append("            **kw,")
    lines.append("        )")
    lines.append("        self.on_stop_calls = 0")
    lines.append("        self.delivery_count = 0")
    lines.append("")
    lines.append("    async def on_start(self):")
    if subscribe_probe:
        lines.append(
            '        await self.core.events.subscribe("probe.ping", self._on_probe, self.metadata.id)'
        )
    else:
        lines.append("        pass")
    lines.append("")
    lines.append("    async def on_stop(self):")
    lines.append("        self.on_stop_calls += 1")
    if subscribe_probe:
        lines.append("")
        lines.append("    async def _on_probe(self, event):")
        lines.append("        self.delivery_count += 1")
    lines.append("")
    lines.append("    async def get_state(self):")
    lines.append('        return {"generation": self.GENERATION}')
    lines.append("")
    lines.append("    async def restore_state(self, state):")
    lines.append("        pass")

    return "\n".join(lines)


def _make_bad_start_code(name: str) -> str:
    """Return code for a plugin whose on_start raises (triggers rollback)."""
    class_name = name.capitalize() + "Plugin"
    lines: list[str] = []

    lines.append(f"class {class_name}(Plugin):")
    lines.append("    GENERATION = -1")
    lines.append("")
    lines.append("    def __init__(self, **kw):")
    lines.append(f"        super().__init__(name={name!r}, **kw)")
    lines.append("        self.on_stop_calls = 0")
    lines.append("        self.delivery_count = 0")
    lines.append("")
    lines.append("    async def on_start(self):")
    lines.append('        raise RuntimeError("bad_start intentional failure")')
    lines.append("")
    lines.append("    async def on_stop(self):")
    lines.append("        self.on_stop_calls += 1")

    return "\n".join(lines)


class HotReloadMachine(RuleBasedStateMachine):
    """Stateful machine that exercises hot reload on a real started Core.

    Model state (per live plugin name):
        generation  - monotonically increasing int; must match GENERATION on the
                      live instance after every successful reload.
        plugin_id   - UUID string; must be stable across reloads (zero-downtime).
        provides    - set[str] of capabilities the current version provides.
        subscribed  - bool; True if the current version subscribes to probe.ping.

    Rules:
        load_fresh        - register a new plugin (name not yet live).
        reload_good       - swap a live plugin with a new valid variant.
        reload_bad_start  - attempt a reload whose on_start raises; expect rollback.
        publish_probe     - publish probe.ping; assert exact delivery counts.
        unregister        - remove a live plugin that has no dependents.
    """

    def __init__(self) -> None:
        super().__init__()
        # Async bridge: one event loop per machine instance.
        self._loop = asyncio.new_event_loop()
        self._core: Core = Core()
        self._loop.run_until_complete(self._core.start())

        # Model: name -> {generation, plugin_id, provides, subscribed}
        self._model: dict[str, dict] = {}

        # Retained old instances after successful reloads, to check on_stop.
        # List of (old_instance, expected_on_stop_calls) tuples.
        self._retired: list[tuple] = []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run(self, coro):
        """Run a coroutine on the machine's event loop."""
        return self._loop.run_until_complete(coro)

    def _live_names(self) -> list[str]:
        return list(self._model.keys())

    def _available_caps(self) -> set[str]:
        """Union of all capabilities currently provided by live plugins."""
        caps: set[str] = set()
        for info in self._model.values():
            caps |= info["provides"]
        return caps

    def teardown(self) -> None:
        """Stop the core and close the loop on machine teardown."""
        try:
            if self._core.state is CoreState.RUNNING:
                self._loop.run_until_complete(self._core.stop())
        finally:
            self._loop.close()

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    @initialize()
    def setup(self) -> None:
        """Machine-level initialize hook (model already set up in __init__)."""

    @rule(
        name=_st_name,
        provides=_st_caps,
        subscribe_probe=st.booleans(),
    )
    def load_fresh(self, name: str, provides: set[str], subscribe_probe: bool) -> None:
        """Register a fresh plugin (name not currently live)."""
        assume(name not in self._model)

        code = _make_plugin_code(
            name=name,
            generation=1,
            provides=provides,
            requires=set(),  # fresh plugins never require to stay satisfiable
            subscribe_probe=subscribe_probe,
        )
        self._run(self._core.load_plugin(code))

        plugin = self._run(self._core.get_plugin(name))
        assert plugin is not None, f"load_fresh: {name} not found after load"

        self._model[name] = {
            "generation": 1,
            "plugin_id": str(plugin.metadata.id),
            "provides": provides,
            "subscribed": subscribe_probe,
        }

    @rule(name=_st_name, subscribe_probe=st.booleans())
    def reload_good(self, name: str, subscribe_probe: bool) -> None:
        """Reload a live plugin with a valid new variant; assert ID stability."""
        assume(name in self._model)

        old_plugin = self._run(self._core.get_plugin(name))
        assert old_plugin is not None

        info = self._model[name]
        new_generation = info["generation"] + 1

        # Keep the same provides set across reloads so no downstream plugin
        # ever loses a required capability between steps.  A full random draw
        # would require tracking dependents in the model; skipping for simplicity.
        new_provides = info["provides"]

        code = _make_plugin_code(
            name=name,
            generation=new_generation,
            provides=new_provides,
            requires=set(),
            subscribe_probe=subscribe_probe,
        )
        self._run(self._core.load_plugin(code))

        new_plugin = self._run(self._core.get_plugin(name))
        assert new_plugin is not None, f"reload_good: {name} missing after reload"

        # Zero-downtime: ID must be stable.
        assert str(new_plugin.metadata.id) == info["plugin_id"], (
            f"reload_good: ID changed for {name}: "
            f"expected {info['plugin_id']}, got {new_plugin.metadata.id}"
        )

        # on_stop must have been called on the old instance exactly once.
        # This invariant covers the kernel fix landing in parallel; if the fix
        # hasn't landed yet, old_plugin.on_stop_calls will be 0 rather than 1.
        self._retired.append(old_plugin)

        # Update model.
        self._model[name] = {
            "generation": new_generation,
            "plugin_id": info["plugin_id"],
            "provides": new_provides,
            "subscribed": subscribe_probe,
        }

    @rule(name=_st_name)
    def reload_bad_start(self, name: str) -> None:
        """Attempt a reload whose on_start raises; model must remain unchanged."""
        assume(name in self._model)

        info = self._model[name]
        old_plugin = self._run(self._core.get_plugin(name))
        assert old_plugin is not None

        code = _make_bad_start_code(name)
        raised = False
        try:
            self._run(self._core.load_plugin(code))
        except RuntimeError as exc:
            raised = True
            if "bad_start intentional failure" not in str(exc):
                raise AssertionError(f"reload_bad_start: unexpected error: {exc}") from exc
        if not raised:
            pytest.fail(f"reload_bad_start: expected RuntimeError for {name}")

        # Old instance still serving, ID unchanged.
        still_live = self._run(self._core.get_plugin(name))
        assert still_live is old_plugin, f"reload_bad_start: old instance replaced for {name}"
        assert str(still_live.metadata.id) == info["plugin_id"], (
            f"reload_bad_start: ID changed despite rollback for {name}"
        )
        # Generation marker must still match the model (old generation serving).
        assert info["generation"] == still_live.__class__.GENERATION, (
            f"reload_bad_start: generation changed despite rollback for {name}: "
            f"expected {info['generation']}, got {still_live.__class__.GENERATION}"
        )
        # on_stop must NOT have been called during rollback.
        assert old_plugin.on_stop_calls == 0, (
            f"reload_bad_start: on_stop called during rollback for {name}"
        )

    @rule()
    def publish_probe(self) -> None:
        """Publish probe.ping; assert exactly-once delivery per subscriber."""
        assume(self._model)  # at least one plugin live

        # Snapshot delivery counts before.
        before: dict[str, int] = {}
        for name in self._live_names():
            plugin = self._run(self._core.get_plugin(name))
            if plugin is not None:
                before[name] = plugin.delivery_count

        from uxok.protocols import Event as OrionEvent

        self._run(self._core.events.publish(OrionEvent("probe.ping", {})))
        # Settle: let the tick loop deliver the event.
        self._run(asyncio.sleep(0.05))

        # Assert exactly-once delivery per subscriber, zero for non-subscribers.
        for name in self._live_names():
            plugin = self._run(self._core.get_plugin(name))
            if plugin is None:
                continue
            after = plugin.delivery_count
            subscribed = self._model[name]["subscribed"]
            if subscribed:
                assert after == before.get(name, 0) + 1, (
                    f"publish_probe: {name} (subscribed) got {after - before.get(name, 0)} "
                    f"deliveries, expected 1"
                )
            else:
                assert after == before.get(name, 0), (
                    f"publish_probe: {name} (not subscribed) got unexpected delivery "
                    f"(before={before.get(name, 0)}, after={after})"
                )

    @rule(name=_st_name)
    def unregister(self, name: str) -> None:
        """Unregister a live plugin (only when no other live plugin requires it)."""
        assume(name in self._model)
        # Only unregister if no other live plugin has this plugin's capabilities
        # in its requires set (which we don't track precisely — use empty requires
        # throughout, so no dependents ever exist).
        self._run(self._core.unregister_plugin(name))
        del self._model[name]

    # ------------------------------------------------------------------
    # Invariants
    # ------------------------------------------------------------------

    @invariant()
    def core_is_running(self) -> None:
        """Core must remain RUNNING throughout every step."""
        assert self._core.state is CoreState.RUNNING, f"Core left RUNNING state: {self._core.state}"

    @invariant()
    def live_plugins_retrievable_with_stable_ids(self) -> None:
        """Every model-tracked plugin must be retrievable, with its ID unchanged."""
        for name, info in self._model.items():
            plugin = self._run(self._core.get_plugin(name))
            assert plugin is not None, f"invariant: live plugin {name!r} not found in core"
            assert str(plugin.metadata.id) == info["plugin_id"], (
                f"invariant: ID changed for {name}: "
                f"model={info['plugin_id']}, core={plugin.metadata.id}"
            )

    @invariant()
    def generation_matches_model(self) -> None:
        """Live instance GENERATION must match the model's generation."""
        for name, info in self._model.items():
            plugin = self._run(self._core.get_plugin(name))
            if plugin is None:
                continue
            assert info["generation"] == plugin.__class__.GENERATION, (
                f"invariant: GENERATION mismatch for {name}: "
                f"model={info['generation']}, live={plugin.__class__.GENERATION}"
            )

    @invariant()
    def provided_capabilities_resolvable_without_duplicates(self) -> None:
        """Every model-declared capability must resolve; no ID appears twice in providers."""
        cs = self._core._capability_system
        for name, info in self._model.items():
            for cap in info["provides"]:
                providers = cs._capabilities.get(cap, [])
                assert len(providers) >= 1, (
                    f"invariant: capability {cap!r} provided by {name!r} not in capability system"
                )
                provider_ids = [str(p.metadata.id) for p in providers]
                assert len(provider_ids) == len(set(provider_ids)), (
                    f"invariant: duplicate provider IDs for capability {cap!r}: {provider_ids}"
                )

    @invariant()
    def no_active_operations_between_steps(self) -> None:
        """The per-plugin operation guard must be empty between rules."""
        active = self._loop.run_until_complete(self._core._active_operations.copy())
        assert len(active) == 0, f"invariant: _active_operations not empty between steps: {active}"

    @invariant()
    def retired_instances_had_on_stop_called(self) -> None:
        """Each old instance replaced by a successful reload must have on_stop_calls == 1.

        Pins the hot-reload contract: a successful swap calls the replaced
        instance's on_stop() exactly once (and never calls it on rollback).
        """
        for old_plugin in self._retired:
            assert old_plugin.on_stop_calls == 1, (
                f"invariant: retired plugin {old_plugin.metadata.name!r} "
                f"(gen={old_plugin.__class__.GENERATION}) has on_stop_calls="
                f"{old_plugin.on_stop_calls}, expected 1"
            )


__all__ = [
    "CoreStateMachine",
    "ErrorRecoveryStateMachine",
    "EventBusStateMachine",
    "HookSystemStateMachine",
    "HotReloadMachine",
    "PluginLifecycleStateMachine",
    "PluginSystemStateMachine",
]
