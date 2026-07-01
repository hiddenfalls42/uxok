"""Tests for Core lifecycle: start/stop, context manager, file loading, capabilities."""

from __future__ import annotations

import pytest

from uxok import Plugin
from uxok.errors import CapabilityError, CoreError
from uxok.protocols import CoreState


class TestCoreLifecycle:
    @pytest.mark.asyncio
    async def test_async_context_manager(self, clean_core):
        async with clean_core as core:
            assert core.state.value == "running"

    @pytest.mark.asyncio
    async def test_stop_with_plugins(self, started_core):
        core = started_core

        class StopPlugin(Plugin):
            stopped = False

            def __init__(self):
                super().__init__(name="stop_test")

            async def on_stop(self):
                StopPlugin.stopped = True

        p = StopPlugin()
        await core.register_plugin(p)
        await core.stop()
        assert StopPlugin.stopped is True

    @pytest.mark.asyncio
    async def test_precache_hooks(self, started_core):
        core = started_core
        p = Plugin(name="precache_test")
        await core.register_plugin(p)
        await core._precache_hooks()  # Should not raise


class TestCoreStopOrder:
    """Verify core.stop() respects dependency ordering."""

    @pytest.mark.asyncio
    async def test_stop_respects_reverse_dependency_order(self, started_core):
        """Plugins should be stopped in reverse dependency order."""
        stop_order = []

        core = started_core

        class BasePlugin(Plugin):
            def __init__(self):
                super().__init__(name="base", provides={"base_capability"})

            async def on_stop(self):
                stop_order.append("base")

        class MiddlePlugin(Plugin):
            def __init__(self):
                super().__init__(
                    name="middle",
                    requires={"base_capability"},
                    provides={"middle_capability"},
                )

            async def on_stop(self):
                stop_order.append("middle")

        class TopPlugin(Plugin):
            def __init__(self):
                super().__init__(name="top", requires={"middle_capability"})

            async def on_stop(self):
                # This should execute while middle and base are still running
                middle = await self.get_capability("middle_capability")
                assert middle is not None
                stop_order.append("top")

        # Register in dependency order (base -> middle -> top)
        base = BasePlugin()
        middle = MiddlePlugin()
        top = TopPlugin()

        await core.register_plugin(base)
        await core.register_plugin(middle)
        await core.register_plugin(top)

        await core.stop()

        # Verify reverse dependency order: top -> middle -> base
        assert stop_order == ["top", "middle", "base"]


class TestCoreCapabilities:
    @pytest.mark.asyncio
    async def test_list_capabilities(self, started_core):
        """Capability discovery via the single public surface: core.list()."""
        core = started_core
        p = Plugin(name="cap_list", provides={"storage", "cache"})
        await core.register_plugin(p)
        caps = (await core.list()).capabilities
        assert "storage" in caps
        assert "cache" in caps

    @pytest.mark.asyncio
    async def test_get_capability_info(self, started_core):
        # get_capability_info is internal (capability-system) detail; no longer
        # on the public Core surface. Verify the subsystem behaviour directly.
        core = started_core
        p = Plugin(name="cap_info", provides={"storage"})
        await core.register_plugin(p)
        info = await core._capability_system.get_capability_info("storage")
        assert info is not None
        assert info["name"] == "storage"

    @pytest.mark.asyncio
    async def test_get_capability_missing_raises(self, clean_core):
        with pytest.raises(CapabilityError):
            await clean_core.get_capability("nonexistent")


class TestCoreProperties:
    @pytest.mark.asyncio
    async def test_properties(self, clean_core):
        core = clean_core
        assert core.config is not None
        assert core._registry is not None
        assert core.events is not None
        assert core.hooks is not None
        assert core.id is not None


class TestStopIsTeardown:
    """core.stop() fully unregisters plugins, leaving an empty reusable core
    (decision #5, revised)."""

    @pytest.mark.asyncio
    async def test_stop_empties_the_registry(self, started_core):
        core = started_core
        await core.register_plugin(Plugin(name="alpha", provides={"a"}))
        await core.register_plugin(Plugin(name="beta", requires={"a"}))

        await core.stop()

        assert await core._registry.all() == {}
        assert len(await core.list()) == 0
        assert (await core.list()).capabilities == []

    @pytest.mark.asyncio
    async def test_on_stop_runs_during_teardown(self, started_core):
        stopped = []

        class Graceful(Plugin):
            def __init__(self):
                super().__init__(name="graceful")

            async def on_stop(self):
                stopped.append(self.metadata.name)

        core = started_core
        await core.register_plugin(Graceful())
        await core.stop()
        assert stopped == ["graceful"]

    @pytest.mark.asyncio
    async def test_restart_accepts_a_fresh_plugin_graph(self, started_core):
        core = started_core
        await core.register_plugin(Plugin(name="first_run"))
        await core.stop()

        # Same Core object, fresh graph: the orchestrator owns reconstruction.
        await core.start()
        fresh = Plugin(name="second_run")
        assert await core.register_plugin(fresh) is True
        assert await core.get_plugin("second_run") is fresh
        assert await core.get_plugin("first_run") is None
        await core.stop()

    @pytest.mark.asyncio
    async def test_teardown_unregisters_dependents_before_dependencies(self, started_core):
        order = []

        class Tracker(Plugin):
            async def on_stop(self):
                order.append(self.metadata.name)

        class Provider(Tracker):
            def __init__(self):
                super().__init__(name="provider", provides={"thing"})

        class Consumer(Tracker):
            def __init__(self):
                super().__init__(name="consumer", requires={"thing"})

        core = started_core
        await core.register_plugin(Provider())
        await core.register_plugin(Consumer())
        await core.stop()

        assert order == ["consumer", "provider"]


class TestDuplicateRegistration:
    """Regression: registering an already-registered plugin must be rejected
    up front. The old behavior fell into the failure rollback, which drained
    the RUNNING plugin's subscriptions, hooks, and capabilities."""

    @pytest.mark.asyncio
    async def test_double_register_raises_and_preserves_plugin(self, started_core):
        import asyncio

        from uxok import event
        from uxok.errors import PluginError
        from uxok.protocols import Event

        received = []

        class DupPlugin(Plugin):
            def __init__(self, **kw):
                super().__init__(name="dup_plugin", provides={"dup_cap"}, **kw)

            @event("test.dup_ping")
            async def on_ping(self, e):
                received.append(e)

        core = started_core
        plugin = DupPlugin()
        assert await core.register_plugin(plugin)

        with pytest.raises(PluginError, match="already registered"):
            await core.register_plugin(plugin)

        # The running plugin is untouched: capability resolvable, handler firing.
        assert await core.get_capability("dup_cap") is plugin
        await core.events.publish(Event("test.dup_ping", {}))
        await asyncio.sleep(0.01)
        assert len(received) == 1


class TestNonRunningCoreRejectsRegistration:
    """Phase 2 regression: register_plugin/load_plugin require a RUNNING core.

    Auto-start was removed; an INITIALIZED or STOPPED core must reject
    registration/load with CoreError, and leave no trace in the registry.
    """

    @pytest.mark.asyncio
    async def test_register_plugin_before_start_raises(self, clean_core):
        """register_plugin on an INITIALIZED core raises CoreError."""
        assert clean_core.state is CoreState.INITIALIZED

        with pytest.raises(CoreError, match="started before registering"):
            await clean_core.register_plugin(Plugin(name="too_early"))

        assert clean_core.state is CoreState.INITIALIZED
        assert await clean_core._registry.all() == {}

    @pytest.mark.asyncio
    async def test_failed_pre_start_register_leaves_no_trace(self, clean_core):
        """A failed pre-start register leaves no plugin attached or started."""
        assert clean_core.state is CoreState.INITIALIZED
        plugin = Plugin(name="ghost")

        with pytest.raises(CoreError):
            await clean_core.register_plugin(plugin)

        # No leak into the registry, and the plugin is not resolvable.
        assert await clean_core._registry.all() == {}
        assert await clean_core.get_plugin("ghost") is None

    @pytest.mark.asyncio
    async def test_load_plugin_before_start_raises(self, clean_core):
        """load_plugin on an INITIALIZED core raises CoreError."""
        assert clean_core.state is CoreState.INITIALIZED

        code = """
class PreStartPlugin(Plugin):
    def __init__(self, **kw):
        super().__init__(name="pre_start", **kw)
"""
        with pytest.raises(CoreError, match="started before loading"):
            await clean_core.load_plugin(code)

        assert await clean_core._registry.all() == {}

    @pytest.mark.asyncio
    async def test_start_then_register_works(self, started_core):
        """register_plugin on a RUNNING core succeeds and attaches the plugin."""
        assert started_core.state is CoreState.RUNNING

        plugin = Plugin(name="after_start", provides={"after_cap"})
        assert await started_core.register_plugin(plugin) is True
        assert await started_core.get_plugin("after_start") is plugin
        assert await started_core.get_capability("after_cap") is plugin
