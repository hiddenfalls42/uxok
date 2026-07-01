"""Tests for Core.load_plugin() method."""

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

from uxok import Core
from uxok.errors import CoreError, PluginError
from uxok.protocols import CoreState, Event


class TestLoadPlugin:
    @pytest.mark.asyncio
    async def test_load_plugin_fresh_registration(self, started_core: Core):
        """load_plugin() registers a new plugin that didn't previously exist."""
        code = """
class TestPlugin(Plugin):
    def __init__(self):
        super().__init__(name="test")
"""
        result = await started_core.load_plugin(code)
        assert result is True

        plugin = await started_core.get_plugin("test")
        assert plugin is not None

    @pytest.mark.asyncio
    async def test_load_plugin_upserts_existing(self, started_core: Core):
        """load_plugin() swaps in a new instance when plugin name already exists."""
        # Load initial version
        code_v1 = """
class TestPlugin(Plugin):
    VERSION = 1
    def __init__(self, **kwargs):
        super().__init__(name="test", **kwargs)
"""
        await started_core.load_plugin(code_v1)

        plugin = await started_core.get_plugin("test")
        assert plugin.__class__.VERSION == 1
        old_id = plugin.metadata.id

        # Load v2 - should swap
        code_v2 = """
class TestPlugin(Plugin):
    VERSION = 2
    def __init__(self, **kwargs):
        super().__init__(name="test", **kwargs)
"""
        await started_core.load_plugin(code_v2)

        plugin = await started_core.get_plugin("test")
        assert plugin.__class__.VERSION == 2
        # ID should be preserved for zero-downtime
        assert plugin.metadata.id == old_id

    @pytest.mark.asyncio
    async def test_load_plugin_no_sys_modules_pollution(self, started_core: Core):
        """Loaded plugin code does not appear in sys.modules."""
        before = set(sys.modules.keys())
        code = """
class TestPlugin(Plugin):
    def __init__(self):
        super().__init__(name="test")
"""
        await started_core.load_plugin(code)
        after = set(sys.modules.keys())

        assert after == before  # No new entries

    @pytest.mark.asyncio
    async def test_load_plugin_preserves_id_on_reload(self, started_core: Core):
        """Plugin ID is preserved across reload (zero-downtime invariant)."""
        code = """
class TestPlugin(Plugin):
    def __init__(self, **kwargs):
        super().__init__(name="test", **kwargs)
"""
        await started_core.load_plugin(code)

        plugin = await started_core.get_plugin("test")
        old_id = plugin.metadata.id

        # Reload
        await started_core.load_plugin(code)

        plugin = await started_core.get_plugin("test")
        assert plugin.metadata.id == old_id

    @pytest.mark.asyncio
    async def test_reload_does_not_duplicate_capability_providers(self, started_core: Core):
        """Repeated reloads must not grow the capability provider list (C1 regression)."""
        code = """
class ProviderPlugin(Plugin):
    def __init__(self, **kwargs):
        super().__init__(name="provider", provides={"thing"}, **kwargs)
"""
        await started_core.load_plugin(code)

        cs = started_core._capability_system
        for _ in range(5):
            await started_core.load_plugin(code)
            providers = cs._capabilities.get("thing", [])
            # Exactly one provider, and it is the single live instance.
            assert len(providers) == 1
            assert len({id(p) for p in providers}) == 1

    @pytest.mark.asyncio
    async def test_reload_reconciles_changed_provides_set(self, started_core: Core):
        """Reload adds new capabilities and drops removed ones (C1 reconcile)."""
        code_v1 = """
class ProviderPlugin(Plugin):
    def __init__(self, **kwargs):
        super().__init__(name="provider", provides={"alpha", "beta"}, **kwargs)
"""
        await started_core.load_plugin(code_v1)
        cs = started_core._capability_system
        assert "alpha" in cs._capabilities
        assert "beta" in cs._capabilities

        # v2 drops "beta", keeps "alpha", adds "gamma"
        code_v2 = """
class ProviderPlugin(Plugin):
    def __init__(self, **kwargs):
        super().__init__(name="provider", provides={"alpha", "gamma"}, **kwargs)
"""
        await started_core.load_plugin(code_v2)

        assert len(cs._capabilities.get("alpha", [])) == 1
        assert len(cs._capabilities.get("gamma", [])) == 1
        # "beta" is no longer provided by anyone -> removed entirely.
        assert "beta" not in cs._capabilities

    @pytest.mark.asyncio
    async def test_failed_reload_rolls_back_cleanly(self, started_core: Core):
        """A reload whose on_start fails must leave the old version fully intact.

        Regression for C2: the half-started new instance's handlers (registered
        under the shared plugin ID) must be drained and the old instance's
        handlers restored — no zombie subscribers.
        """
        code_v1 = """
from uxok import event

class Rp(Plugin):
    def __init__(self, **kw):
        super().__init__(name="rp", **kw)
        self.seen = []

    @event("y.ping")
    async def h(self, ev):
        self.seen.append("v1")
"""
        code_v2 = """
from uxok import event

class Rp(Plugin):
    def __init__(self, **kw):
        super().__init__(name="rp", **kw)
        self.seen = []

    @event("y.ping")
    async def h(self, ev):
        self.seen.append("v2-zombie")

    async def on_start(self):
        raise ValueError("boom v2")
"""
        await started_core.load_plugin(code_v1)
        v1 = await started_core.get_plugin("rp")

        with pytest.raises(ValueError, match="boom v2"):
            await started_core.load_plugin(code_v2)

        # Exactly one live subscriber: the restored v1 handler.
        subscribers = started_core._event_bus._subscriptions.get_subscribers("y.ping")
        assert len(subscribers) == 1

        # v1 is still the registered instance and still functional.
        assert await started_core.get_plugin("rp") is v1
        await started_core.events.publish(Event("y.ping", {}))
        await asyncio.sleep(0.05)
        assert v1.seen == ["v1"]

        await started_core.stop()

    @pytest.mark.asyncio
    async def test_reload_succeeds_after_failed_reload(self, started_core: Core):
        """A failed reload must not poison subsequent reload attempts."""
        code_v1 = """
class Rp(Plugin):
    VERSION = 1
    def __init__(self, **kw):
        super().__init__(name="rp", **kw)
"""
        code_broken = """
class Rp(Plugin):
    def __init__(self, **kw):
        super().__init__(name="rp", **kw)
    async def on_start(self):
        raise ValueError("boom")
"""
        code_v3 = """
class Rp(Plugin):
    VERSION = 3
    def __init__(self, **kw):
        super().__init__(name="rp", **kw)
"""
        await started_core.load_plugin(code_v1)
        old_id = (await started_core.get_plugin("rp")).metadata.id

        with pytest.raises(ValueError, match="boom"):
            await started_core.load_plugin(code_broken)

        await started_core.load_plugin(code_v3)
        plugin = await started_core.get_plugin("rp")
        assert plugin.__class__.VERSION == 3
        assert plugin.metadata.id == old_id

        await started_core.stop()

    @pytest.mark.asyncio
    async def test_load_plugin_raises_on_no_class(self, started_core: Core):
        """Raises PluginError if code contains no Plugin subclass."""
        code = """
class NotAPlugin:
    pass
"""
        with pytest.raises(PluginError, match="No Plugin subclass found"):
            await started_core.load_plugin(code)

    @pytest.mark.asyncio
    async def test_load_plugin_raises_on_multiple_classes(self, started_core: Core):
        """Raises PluginError if code contains more than one Plugin subclass."""
        code = """
class Plugin1(Plugin):
    def __init__(self):
        super().__init__(name="plugin1")

class Plugin2(Plugin):
    def __init__(self):
        super().__init__(name="plugin2")
"""
        with pytest.raises(PluginError, match="Multiple Plugin subclasses found"):
            await started_core.load_plugin(code)

    @pytest.mark.asyncio
    async def test_load_plugin_emits_reloaded_event(self, started_core: Core):
        """core.plugin_reloaded event is emitted when a plugin is swapped."""
        code = """
class TestPlugin(Plugin):
    def __init__(self, **kwargs):
        super().__init__(name="test", **kwargs)
"""

        # Load initial
        await started_core.load_plugin(code)

        # Track events
        events = []

        async def track(event):
            events.append(event)

        await started_core.events.subscribe("core.plugin_reloaded", track)

        await started_core.load_plugin(code)
        await asyncio.sleep(0.005)

        assert len(events) == 1
        assert events[0].data["plugin_name"] == "test"

    @pytest.mark.asyncio
    async def test_load_plugin_from_file(self, started_core: Core):
        """Demonstrate that file loading is a user concern — just read and pass."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("""
class FilePlugin(Plugin):
    def __init__(self, **kwargs):
        super().__init__(name="file_plugin", **kwargs)
""")
            f.flush()
            path = f.name

        try:
            code = Path(path).read_text()
            await started_core.load_plugin(code)

            plugin = await started_core.get_plugin("file_plugin")
            assert plugin is not None
        finally:
            Path(path).unlink()

    @pytest.mark.asyncio
    async def test_load_plugin_with_config(self):
        """Plugin config comes from CoreConfig.plugin_configs."""
        core = Core(plugin_configs={"test": {"db_url": "postgres://localhost", "timeout": 30}})

        code = """
from uxok import Plugin
from uxok.plugin import ConfigField, REQUIRED

class TestPlugin(Plugin):
    def __init__(self):
        super().__init__(name="test",
            config_schema={
                "db_url": ConfigField(str, REQUIRED),
                "timeout": ConfigField(int, default=10),
            }
        )

    async def on_start(self):
        self.db_url = self.config("db_url")
        self.timeout = self.config("timeout")
"""
        try:
            await core.start()
            await core.load_plugin(code)

            plugin = await core.get_plugin("test")
            await plugin.start()
            assert plugin.db_url == "postgres://localhost"
            assert plugin.timeout == 30
        finally:
            if core.state.name == "RUNNING":
                await core.stop()


class TestStateHandoff:
    """Hot-reload state handoff via get_state()/restore_state()."""

    CODE_COUNTER_V1 = """
class Counter(Plugin):
    VERSION = 1
    def __init__(self, **kw):
        super().__init__(name="counter", **kw)
        self.count = 0

    async def get_state(self):
        return {"count": self.count}

    async def restore_state(self, state):
        self.count = state.get("count", 0)
"""
    CODE_COUNTER_V2 = CODE_COUNTER_V1.replace("VERSION = 1", "VERSION = 2")

    @pytest.mark.asyncio
    async def test_state_survives_reload(self, started_core: Core):
        await started_core.load_plugin(self.CODE_COUNTER_V1)
        v1 = await started_core.get_plugin("counter")
        v1.count = 42

        await started_core.load_plugin(self.CODE_COUNTER_V2)
        v2 = await started_core.get_plugin("counter")

        assert v2.__class__.VERSION == 2
        assert v2.count == 42
        await started_core.stop()

    @pytest.mark.asyncio
    async def test_default_noop_contract(self, started_core: Core):
        """Plugins that don't implement the contract reload with fresh state."""
        code = """
class Fresh(Plugin):
    def __init__(self, **kw):
        super().__init__(name="fresh", **kw)
        self.value = "initial"
"""
        await started_core.load_plugin(code)
        (await started_core.get_plugin("fresh")).value = "mutated"

        await started_core.load_plugin(code)
        assert (await started_core.get_plugin("fresh")).value == "initial"
        await started_core.stop()

    @pytest.mark.asyncio
    async def test_get_state_failure_aborts_reload_cleanly(self, started_core: Core):
        code_v1 = """
class Sick(Plugin):
    def __init__(self, **kw):
        super().__init__(name="sick", **kw)

    async def get_state(self):
        raise RuntimeError("state capture failed")
"""
        await started_core.load_plugin(code_v1)
        v1 = await started_core.get_plugin("sick")

        with pytest.raises(RuntimeError, match="state capture failed"):
            await started_core.load_plugin(code_v1)

        # Old instance untouched and still registered.
        assert await started_core.get_plugin("sick") is v1
        await started_core.stop()

    @pytest.mark.asyncio
    async def test_restore_state_failure_rolls_back(self, started_core: Core):
        code_v1 = self.CODE_COUNTER_V1
        code_v2 = """
class Counter(Plugin):
    def __init__(self, **kw):
        super().__init__(name="counter", **kw)
        self.count = 0

    async def restore_state(self, state):
        raise ValueError("cannot ingest state")
"""
        await started_core.load_plugin(code_v1)
        v1 = await started_core.get_plugin("counter")
        v1.count = 7

        with pytest.raises(ValueError, match="cannot ingest state"):
            await started_core.load_plugin(code_v2)

        assert await started_core.get_plugin("counter") is v1
        assert v1.count == 7
        await started_core.stop()


class TestReloadRequiresRevalidation:
    """Hot reload validates the new version's requires and reconciles edges (H8)."""

    @pytest.mark.asyncio
    async def test_reload_with_missing_requirement_fails_fast(self, started_core: Core):
        code_v1 = """
class Needy(Plugin):
    def __init__(self, **kw):
        super().__init__(name="needy", **kw)
"""
        code_v2 = """
class Needy(Plugin):
    def __init__(self, **kw):
        super().__init__(name="needy", requires={"absent_capability"}, **kw)
"""
        await started_core.load_plugin(code_v1)
        v1 = await started_core.get_plugin("needy")

        from uxok.errors import MissingCapabilityError

        with pytest.raises(MissingCapabilityError):
            await started_core.load_plugin(code_v2)

        # Old version still serving.
        assert await started_core.get_plugin("needy") is v1
        await started_core.stop()

    @pytest.mark.asyncio
    async def test_reload_adding_requirement_creates_edge(self, started_core: Core):
        provider_code = """
class Storage(Plugin):
    def __init__(self, **kw):
        super().__init__(name="storage", provides={"storage"}, **kw)
"""
        consumer_v1 = """
class Consumer(Plugin):
    def __init__(self, **kw):
        super().__init__(name="consumer", **kw)
"""
        consumer_v2 = """
class Consumer(Plugin):
    def __init__(self, **kw):
        super().__init__(name="consumer", requires={"storage"}, **kw)
"""
        await started_core.load_plugin(provider_code)
        await started_core.load_plugin(consumer_v1)

        consumer = await started_core.get_plugin("consumer")
        assert await started_core._registry.dependencies(consumer.metadata.id) == set()

        await started_core.load_plugin(consumer_v2)

        storage = await started_core.get_plugin("storage")
        consumer = await started_core.get_plugin("consumer")
        deps = await started_core._registry.dependencies(consumer.metadata.id)
        assert deps == {storage.metadata.id}

        # The provider is now blocked from plain unregistration.
        from uxok.errors import PluginError

        with pytest.raises(PluginError, match="dependents present"):
            await started_core.unregister_plugin("storage")
        await started_core.stop()

    @pytest.mark.asyncio
    async def test_reload_dropping_requirement_frees_dependency(self, started_core: Core):
        provider_code = """
class Storage(Plugin):
    def __init__(self, **kw):
        super().__init__(name="storage", provides={"storage"}, **kw)
"""
        consumer_v1 = """
class Consumer(Plugin):
    def __init__(self, **kw):
        super().__init__(name="consumer", requires={"storage"}, **kw)
"""
        consumer_v2 = """
class Consumer(Plugin):
    def __init__(self, **kw):
        super().__init__(name="consumer", **kw)
"""
        await started_core.load_plugin(provider_code)
        await started_core.load_plugin(consumer_v1)

        await started_core.load_plugin(consumer_v2)

        consumer = await started_core.get_plugin("consumer")
        assert await started_core._registry.dependencies(consumer.metadata.id) == set()

        # Dropping the edge frees the provider for unregistration.
        assert await started_core.unregister_plugin("storage") is True
        await started_core.stop()


class TestCompileFailure:
    """Compile errors are reported cleanly without touching the running plugin."""

    @pytest.mark.asyncio
    async def test_compile_failure_raises_and_leaves_existing_plugin(self, started_core: Core):
        """Syntactically invalid code raises PluginError; the existing plugin keeps serving.

        Targets _core.py:362-363: the except-around-exec path.
        """
        # Register a healthy plugin under a known name first.
        await started_core.load_plugin(
            """
class Sentinel(Plugin):
    def __init__(self, **kw):
        super().__init__(name="sentinel", **kw)
"""
        )
        sentinel_before = await started_core.get_plugin("sentinel")
        assert sentinel_before is not None

        with pytest.raises(PluginError, match="Failed to compile"):
            await started_core.load_plugin("def broken(: syntax error here")

        # Sentinel is still registered and is the same instance.
        sentinel_after = await started_core.get_plugin("sentinel")
        assert sentinel_after is sentinel_before


class TestOperationGuard:
    """Operation guard serialises concurrent operations on the same plugin ID.

    These tests pre-seed the guard to exercise the PluginError branches at
    _core.py:232 (register_plugin path) and :547 (reload path).
    """

    @pytest.mark.asyncio
    async def test_load_plugin_raises_when_operation_in_flight(self, started_core: Core):
        """load_plugin of an existing plugin raises PluginError when the guard is held."""
        await started_core.load_plugin(
            """
class Guarded(Plugin):
    def __init__(self, **kw):
        super().__init__(name="guarded", **kw)
"""
        )
        plugin = await started_core.get_plugin("guarded")
        plugin_id = plugin.metadata.id

        # Pre-seed the guard to simulate a concurrent in-flight operation.
        await started_core._active_operations.add(plugin_id)
        try:
            with pytest.raises(PluginError, match="already has an active operation"):
                await started_core.load_plugin(
                    """
class Guarded(Plugin):
    def __init__(self, **kw):
        super().__init__(name="guarded", **kw)
"""
                )
        finally:
            await started_core._active_operations.remove(plugin_id)

    @pytest.mark.asyncio
    async def test_register_plugin_raises_when_operation_in_flight(self, started_core: Core):
        """register_plugin raises PluginError when the target ID has an in-flight operation."""
        from uxok import Plugin as _Plugin

        class _FreshPlugin(_Plugin):
            def __init__(self):
                super().__init__(name="fresh_op_guard")

        plugin = _FreshPlugin()
        plugin_id = plugin.metadata.id

        # Seed the guard BEFORE registration so the guard check fires.
        await started_core._active_operations.add(plugin_id)
        try:
            with pytest.raises(PluginError, match="already has an active operation"):
                await started_core.register_plugin(plugin)
        finally:
            await started_core._active_operations.remove(plugin_id)


class TestNonRunningCoreLoadPlugin:
    """load_plugin is now rejected on any non-RUNNING core.

    Phase 2 of the auto-start removal: both register_plugin and load_plugin
    raise CoreError unless the core is RUNNING. INITIALIZED and STOPPED
    cores both reject, and no plugin leaks into the registry on failure.
    """

    @pytest.mark.asyncio
    async def test_load_plugin_on_initialized_core_raises(self, clean_core: Core):
        """load_plugin on a never-started (INITIALIZED) core raises CoreError.

        The core must be RUNNING; nothing is registered as a side effect.
        """
        assert clean_core.state is CoreState.INITIALIZED

        with pytest.raises(CoreError, match="started before loading"):
            await clean_core.load_plugin(
                """
class Rejected(Plugin):
    def __init__(self, **kw):
        super().__init__(name="rejected", **kw)
"""
            )

        assert clean_core.state is CoreState.INITIALIZED
        assert await clean_core._registry.all() == {}

    @pytest.mark.asyncio
    async def test_load_plugin_on_stopped_core_raises(self, clean_core: Core):
        """load_plugin on a STOPPED core raises CoreError (RUNNING required)."""
        # Bring the core to a STOPPED state via start() then stop().
        await clean_core.start()
        await clean_core.stop()
        assert clean_core.state is CoreState.STOPPED

        with pytest.raises(CoreError, match="started before loading"):
            await clean_core.load_plugin(
                """
class LatePlugin(Plugin):
    def __init__(self, **kw):
        super().__init__(name="late", **kw)
"""
            )

        assert clean_core.state is CoreState.STOPPED
        assert await clean_core._registry.all() == {}


class TestCycleDetectionOnReload:
    """Reloading a plugin whose new dependency set would close a cycle must fail.

    Targets registry/impl.py _check_circular_dependencies (~:445-478).
    The cycle check raises PluginError; the old version remains registered
    with its original dependency edges.
    """

    @pytest.mark.asyncio
    async def test_reload_creating_cycle_raises_and_rolls_back(self, started_core: Core):
        """Reload that creates a dependency cycle raises PluginError; original stays."""
        # A provides "alpha_cap"; B requires "alpha_cap" and provides "beta_cap".
        # Reloading A to also require "beta_cap" closes the cycle A->B->A.
        await started_core.load_plugin(
            """
class PluginA(Plugin):
    def __init__(self, **kw):
        super().__init__(name="plugin_a", provides={"alpha_cap"}, **kw)
"""
        )
        await started_core.load_plugin(
            """
class PluginB(Plugin):
    def __init__(self, **kw):
        super().__init__(name="plugin_b",
            requires={"alpha_cap"}, provides={"beta_cap"}, **kw
        )
"""
        )
        v1_a = await started_core.get_plugin("plugin_a")

        with pytest.raises(PluginError, match="Circular dependency detected"):
            await started_core.load_plugin(
                """
class PluginA(Plugin):
    def __init__(self, **kw):
        # Adding requires={"beta_cap"} creates cycle A->B->A.
        super().__init__(name="plugin_a",
            provides={"alpha_cap"}, requires={"beta_cap"}, **kw
        )
"""
            )

        # Original A still registered and serving.
        assert await started_core.get_plugin("plugin_a") is v1_a

        # alpha_cap resolves to the original A instance.
        cap = await started_core.get_capability("alpha_cap")
        assert cap is v1_a

        # Dependency edges are intact: B still depends on A (edge B->A).
        b = await started_core.get_plugin("plugin_b")
        a_id = v1_a.metadata.id
        deps = await started_core._registry.dependencies(b.metadata.id)
        assert a_id in deps


class TestSwapProviderEdgeBranches:
    """Coverage for capability_system.swap_provider edge cases.

    Targets _capability_system.py:350,357,367->365,372,378,389.
    """

    @pytest.mark.asyncio
    async def test_reload_dropping_all_capabilities_prunes_registry(self, started_core: Core):
        """When the new version provides no capabilities, old entries are pruned.

        The plugin remains registered; the capability is no longer resolvable.
        """
        await started_core.load_plugin(
            """
class ProvPlugin(Plugin):
    def __init__(self, **kw):
        super().__init__(name="prov", provides={"droppable_cap"}, **kw)
"""
        )
        cs = started_core._capability_system
        assert len(cs._capabilities.get("droppable_cap", [])) == 1

        await started_core.load_plugin(
            """
class ProvPlugin(Plugin):
    def __init__(self, **kw):
        super().__init__(name="prov", **kw)  # no provides
"""
        )

        # Entry removed entirely from the capability table.
        assert "droppable_cap" not in cs._capabilities
        # Plugin still registered.
        assert await started_core.get_plugin("prov") is not None
        # Capability no longer resolvable.
        from uxok.errors import CapabilityError

        with pytest.raises(CapabilityError):
            await started_core.get_capability("droppable_cap")

    @pytest.mark.asyncio
    async def test_reload_one_of_two_providers_leaves_other_intact(self, started_core: Core):
        """With two providers for the same capability, reloading one leaves the other.

        No duplicate IDs in the provider list after reload.
        """
        await started_core.load_plugin(
            """
class PluginA(Plugin):
    def __init__(self, **kw):
        super().__init__(name="provider_a", provides={"shared_cap"}, **kw)
"""
        )
        await started_core.load_plugin(
            """
class PluginB(Plugin):
    def __init__(self, **kw):
        super().__init__(name="provider_b", provides={"shared_cap"}, **kw)
"""
        )
        b_before = await started_core.get_plugin("provider_b")
        cs = started_core._capability_system
        assert len(cs._capabilities.get("shared_cap", [])) == 2

        # Reload A.
        await started_core.load_plugin(
            """
class PluginA(Plugin):
    def __init__(self, **kw):
        super().__init__(name="provider_a", provides={"shared_cap"}, **kw)
"""
        )

        providers = cs._capabilities.get("shared_cap", [])
        # Still exactly two providers.
        assert len(providers) == 2
        # No duplicate IDs.
        ids = [str(p.metadata.id) for p in providers]
        assert len(set(ids)) == len(ids)
        # B is still the same instance — untouched.
        b_after = await started_core.get_plugin("provider_b")
        assert b_after is b_before
        assert any(p is b_before for p in providers)

    @pytest.mark.asyncio
    async def test_swap_provider_id_mismatch_raises(self, started_core: Core):
        """swap_provider() raises ValueError when the two instances have different IDs.

        This is an internal-contract check: the caller (hot-reload path) must
        always pass same-ID instances. Testing it directly via the internal API
        because load_plugin enforces ID equality before reaching swap_provider.
        """
        await started_core.load_plugin(
            """
class PluginA(Plugin):
    def __init__(self, **kw):
        super().__init__(name="mismatch_a", provides={"cap_a"}, **kw)
"""
        )
        await started_core.load_plugin(
            """
class PluginB(Plugin):
    def __init__(self, **kw):
        super().__init__(name="mismatch_b", provides={"cap_b"}, **kw)
"""
        )
        plugin_a = await started_core.get_plugin("mismatch_a")
        plugin_b = await started_core.get_plugin("mismatch_b")
        assert plugin_a.metadata.id != plugin_b.metadata.id

        with pytest.raises(ValueError, match="Cannot swap providers with different IDs"):
            await started_core._capability_system.swap_provider(plugin_a, plugin_b)


class TestReloadLeakInvariants:
    """Repeated reload cycles must not accumulate subscriptions, hooks, or capabilities.

    Regression guard for resource leaks across hot-reload cycles.
    """

    CODE_RICH = """
from uxok import event, hook

class RichPlugin(Plugin):
    def __init__(self, **kw):
        super().__init__(name="rich", provides={"rich_cap"}, **kw)

    @event("cycle.test")
    async def on_test(self, ev):
        pass

    @hook("cycle.hook")
    async def on_hook(self, **kw):
        return "result"
"""

    @pytest.mark.asyncio
    async def test_no_resource_leak_across_ten_reload_cycles(self, started_core: Core):
        """Subscriptions, hooks, capabilities, sys.modules, and active_operations
        are all stable across 10 load_plugin reload cycles."""
        await started_core.load_plugin(self.CODE_RICH)

        sub_count = len(started_core._event_bus._subscriptions._subscriptions_by_id)
        hook_count = sum(len(v) for v in started_core._hook_system._hooks.values())
        cap_count = len(started_core._capability_system._capabilities.get("rich_cap", []))
        modules_count = len(sys.modules)

        assert sub_count == 1
        assert hook_count == 1
        assert cap_count == 1

        for _ in range(10):
            await started_core.load_plugin(self.CODE_RICH)

        assert len(started_core._event_bus._subscriptions._subscriptions_by_id) == sub_count
        assert sum(len(v) for v in started_core._hook_system._hooks.values()) == hook_count
        assert len(started_core._capability_system._capabilities.get("rich_cap", [])) == cap_count
        assert len(sys.modules) == modules_count
        # Guard must be empty — no stale in-flight markers.
        assert (await started_core._active_operations.copy()) == set()


class TestLoadPluginFromPackageFolder:
    """load_plugin(code, origin=...) loads the file as a package rooted at its
    folder, so a capability can import sibling helper modules relatively — and
    sys.modules stays clean afterward (no permanent pollution)."""

    @pytest.mark.asyncio
    async def test_relative_sibling_import_via_origin(self, started_core: Core, tmp_path: Path):
        """`from . import _helper` resolves to a sibling file when origin is given."""
        (tmp_path / "_helper.py").write_text("VALUE = 'from-sibling'\n")
        entry = tmp_path / "packaged.py"
        entry.write_text(
            "from . import _helper\n"
            "class PackagedPlugin(Plugin):\n"
            "    def __init__(self, **kw):\n"
            "        super().__init__(name='packaged', **kw)\n"
            "        self.helper_value = _helper.VALUE\n"
        )
        before = set(sys.modules)
        await started_core.load_plugin(entry.read_text(), origin=str(entry))

        plugin = await started_core.get_plugin("packaged")
        assert plugin.helper_value == "from-sibling"
        # Invariant: the synthetic package and its sibling are gone from sys.modules.
        assert set(sys.modules) == before
        await started_core.stop()

    @pytest.mark.asyncio
    async def test_from_sibling_import_form_via_origin(self, started_core: Core, tmp_path: Path):
        """`from ._helper import NAME` also resolves the sibling."""
        (tmp_path / "_h.py").write_text("NAME = 'sibling-name'\n")
        entry = tmp_path / "pkgcap.py"
        entry.write_text(
            "from ._h import NAME\n"
            "class PkgCap(Plugin):\n"
            "    def __init__(self, **kw):\n"
            "        super().__init__(name='pkgcap', **kw)\n"
            "        self.name_value = NAME\n"
        )
        await started_core.load_plugin(entry.read_text(), origin=str(entry))
        plugin = await started_core.get_plugin("pkgcap")
        assert plugin.name_value == "sibling-name"
        await started_core.stop()

    @pytest.mark.asyncio
    async def test_dunder_file_is_set_under_origin(self, started_core: Core, tmp_path: Path):
        """Under origin, the module's __file__ is the real path (no NameError)."""
        entry = tmp_path / "filecap.py"
        entry.write_text(
            "from pathlib import Path\n"
            "_HERE = str(Path(__file__).parent)\n"
            "class FileCap(Plugin):\n"
            "    def __init__(self, **kw):\n"
            "        super().__init__(name='filecap', **kw)\n"
            "        self.here = _HERE\n"
        )
        await started_core.load_plugin(entry.read_text(), origin=str(entry))
        plugin = await started_core.get_plugin("filecap")
        assert plugin.here == str(tmp_path)
        await started_core.stop()


class TestCapabilityRevocationEvents:
    """RFC 0001 §3.4: kernel publishes capability lifecycle events on swap/unregister."""

    PROV = """
class ProvPlugin(Plugin):
    def __init__(self, **kw):
        super().__init__(name="prov", provides={"revocable_cap"}, **kw)
"""

    @pytest.mark.asyncio
    async def test_reload_emits_rebound_event(self, started_core: Core):
        """Reloading a provider emits core.capability.rebound for the replaced provider."""
        await started_core.load_plugin(self.PROV)

        events = []

        async def track(event):
            events.append(event)

        await started_core.events.subscribe("core.capability.rebound", track)

        await started_core.load_plugin(self.PROV)
        await asyncio.sleep(0.005)

        assert len(events) == 1
        data = events[0].data
        assert data["capability"] == "revocable_cap"
        # Same-id reload: old and new provider ids are identical by construction.
        assert data["old_provider_id"] == data["new_provider_id"]

    @pytest.mark.asyncio
    async def test_reload_adding_new_capability_emits_no_rebound(self, started_core: Core):
        """A capability the new version *adds* is a fresh registration, not a rebind."""
        await started_core.load_plugin(self.PROV)

        events = []

        async def track(event):
            events.append(event)

        await started_core.events.subscribe("core.capability.rebound", track)

        # Reload provides an additional, previously-absent capability.
        await started_core.load_plugin(
            """
class ProvPlugin(Plugin):
    def __init__(self, **kw):
        super().__init__(name="prov", provides={"revocable_cap", "brand_new_cap"}, **kw)
"""
        )
        await asyncio.sleep(0.005)

        rebound_caps = {e.data["capability"] for e in events}
        assert "revocable_cap" in rebound_caps
        assert "brand_new_cap" not in rebound_caps

    @pytest.mark.asyncio
    async def test_unregister_sole_provider_emits_revoked(self, started_core: Core):
        """Unregistering the last provider of a capability emits core.capability.revoked."""
        await started_core.load_plugin(self.PROV)

        events = []

        async def track(event):
            events.append(event)

        await started_core.events.subscribe("core.capability.revoked", track)

        plugin = await started_core.get_plugin("prov")
        pid = str(plugin.metadata.id)
        await started_core.unregister_plugin("prov")
        await asyncio.sleep(0.005)

        assert len(events) == 1
        assert events[0].data["capability"] == "revocable_cap"
        assert events[0].data["old_provider_id"] == pid

    @pytest.mark.asyncio
    async def test_unregister_with_other_provider_emits_no_revoked(self, started_core: Core):
        """A capability that still has a provider after unregister is not revoked."""
        await started_core.load_plugin(
            """
class PluginA(Plugin):
    def __init__(self, **kw):
        super().__init__(name="prov_a", provides={"shared_cap"}, **kw)
"""
        )
        await started_core.load_plugin(
            """
class PluginB(Plugin):
    def __init__(self, **kw):
        super().__init__(name="prov_b", provides={"shared_cap"}, **kw)
"""
        )

        events = []

        async def track(event):
            events.append(event)

        await started_core.events.subscribe("core.capability.revoked", track)

        await started_core.unregister_plugin("prov_a")
        await asyncio.sleep(0.005)

        # shared_cap still has provider B — no revocation.
        assert events == []

    @pytest.mark.asyncio
    async def test_failed_register_emits_no_revoked(self, started_core: Core):
        """A plugin that fails during start rolls back without announcing revocation."""
        events = []

        async def track(event):
            events.append(event)

        await started_core.events.subscribe("core.capability.revoked", track)

        with pytest.raises(RuntimeError, match="boom during start"):
            await started_core.load_plugin(
                """
class FailingPlugin(Plugin):
    def __init__(self, **kw):
        super().__init__(name="failing", provides={"never_lands"}, **kw)

    async def on_start(self):
        raise RuntimeError("boom during start")
"""
            )
        await asyncio.sleep(0.005)

        assert events == []
