"""Contract tests for old-instance on_stop() during hot reload.

Spec (kernel change landing in parallel):
- During load_plugin() when a plugin with the same name exists, the OLD instance's
  on_stop() is called EXACTLY ONCE after a successful swap.
- on_stop is NEVER called on rollback (v2 on_start raises, or v2 restore_state raises).
- A raising on_stop never fails the reload: the swap is still committed, new version
  serves, and a core.plugin_error event is emitted with:
      source == "lifecycle", phase == "on_stop"
- After a successful reload the kernel sets old._shutdown = True, so a later
  old_instance.stop() call is a no-op (prevents double on_stop).

All tests use the `started_core` fixture from conftest.py.
Event dispatch is tick-gated; we wait with helpers.wait_until, never bare sleep.
"""

import pytest

from tests.helpers import EventCollectingPlugin, wait_until

# ---------------------------------------------------------------------------
# Plugin source strings – used with core.load_plugin()
# Reload matches by name ("widget"), so all versions share name="widget".
# ---------------------------------------------------------------------------

_WIDGET_V1 = """
class Widget(Plugin):
    def __init__(self, **kw):
        super().__init__(name="widget", provides={"widgeting"}, **kw)
        self.on_stop_calls = 0

    async def on_start(self):
        self.active = True

    async def on_stop(self):
        self.on_stop_calls += 1
        self.active = False

    def version(self):
        return 1
"""

_WIDGET_V2 = """
class Widget(Plugin):
    def __init__(self, **kw):
        super().__init__(name="widget", provides={"widgeting"}, **kw)
        self.on_stop_calls = 0

    async def on_start(self):
        self.active = True

    async def on_stop(self):
        self.on_stop_calls += 1
        self.active = False

    def version(self):
        return 2
"""

# v2 whose on_start raises — triggers rollback.
_WIDGET_V2_BAD_START = """
class Widget(Plugin):
    def __init__(self, **kw):
        super().__init__(name="widget", provides={"widgeting"}, **kw)

    async def on_start(self):
        raise RuntimeError("v2 on_start failed")
"""

# v1 that implements get_state so restore_state can be tested.
_WIDGET_V1_WITH_STATE = """
class Widget(Plugin):
    def __init__(self, **kw):
        super().__init__(name="widget", **kw)
        self.on_stop_calls = 0
        self.value = 0

    async def on_stop(self):
        self.on_stop_calls += 1

    async def get_state(self):
        return {"value": self.value}
"""

# v2 whose restore_state raises — triggers rollback after a successful start.
_WIDGET_V2_BAD_RESTORE = """
class Widget(Plugin):
    def __init__(self, **kw):
        super().__init__(name="widget", **kw)

    async def restore_state(self, state):
        raise ValueError("v2 restore_state failed")
"""

# v1 whose on_stop raises — reload must still commit.
_WIDGET_V1_RAISING_STOP = """
class Widget(Plugin):
    def __init__(self, **kw):
        super().__init__(name="widget", provides={"widgeting"}, **kw)
        self.on_stop_calls = 0

    async def on_stop(self):
        self.on_stop_calls += 1
        raise RuntimeError("on_stop exploded")

    def version(self):
        return 1
"""


class TestReloadOnStopContract:
    """on_stop() lifecycle contract for the old plugin instance during hot reload."""

    # ------------------------------------------------------------------
    # 1. Called exactly once on success
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_old_on_stop_called_exactly_once_on_success(self, started_core):
        """Successful reload calls the old instance's on_stop exactly once.

        The new instance's on_stop must NOT be called during the swap.
        """
        await started_core.load_plugin(_WIDGET_V1)
        v1 = await started_core.get_plugin("widget")
        assert v1.on_stop_calls == 0

        await started_core.load_plugin(_WIDGET_V2)
        v2 = await started_core.get_plugin("widget")

        # v1's on_stop was called exactly once during the swap.
        assert v1.on_stop_calls == 1
        # v2 is now serving; its on_stop has not been called.
        assert v2.on_stop_calls == 0
        assert v2.version() == 2

    # ------------------------------------------------------------------
    # 2. Not called on on_start rollback
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_on_stop_not_called_on_start_rollback(self, started_core):
        """When v2 on_start raises, the reload rolls back and v1's on_stop is NOT called."""
        await started_core.load_plugin(_WIDGET_V1)
        v1 = await started_core.get_plugin("widget")

        with pytest.raises(RuntimeError, match="v2 on_start failed"):
            await started_core.load_plugin(_WIDGET_V2_BAD_START)

        # Rollback: v1 is still serving.
        assert await started_core.get_plugin("widget") is v1
        # on_stop must NOT have been called on rollback.
        assert v1.on_stop_calls == 0

    # ------------------------------------------------------------------
    # 3. Not called on restore_state rollback
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_on_stop_not_called_on_restore_state_rollback(self, started_core):
        """When v2 restore_state raises, the reload rolls back and v1's on_stop is NOT called."""
        await started_core.load_plugin(_WIDGET_V1_WITH_STATE)
        v1 = await started_core.get_plugin("widget")

        with pytest.raises(ValueError, match="v2 restore_state failed"):
            await started_core.load_plugin(_WIDGET_V2_BAD_RESTORE)

        assert await started_core.get_plugin("widget") is v1
        assert v1.on_stop_calls == 0

    # ------------------------------------------------------------------
    # 4. Raising on_stop never fails the reload
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_raising_on_stop_does_not_fail_reload(self, started_core):
        """A raising on_stop must not abort the reload.

        Expected post-conditions:
        - load_plugin returns without raising
        - new version is serving
        - core.plugin_reloaded event observed
        - core.plugin_error event observed with source="lifecycle", phase="on_stop"
        """
        # Subscribe to events BEFORE the reload so we catch them.
        # register_plugin calls start() internally; no separate start() needed.
        collector = EventCollectingPlugin(
            name="collector",
            subscribe_to="core.plugin_reloaded",
        )
        await started_core.register_plugin(collector)

        error_events: list = []

        async def catch_plugin_error(event):
            error_events.append(event)

        await started_core.events.subscribe("core.plugin_error", catch_plugin_error)

        await started_core.load_plugin(_WIDGET_V1_RAISING_STOP)
        v1 = await started_core.get_plugin("widget")
        assert v1.on_stop_calls == 0

        # Reload must not raise even though v1.on_stop() will raise.
        await started_core.load_plugin(_WIDGET_V2)
        v2 = await started_core.get_plugin("widget")

        # v1 on_stop was attempted exactly once (even though it raised).
        assert v1.on_stop_calls == 1
        # New version is live.
        assert v2.version() == 2

        # Wait for tick-gated event delivery.
        await wait_until(lambda: len(collector.events_received) >= 1)
        assert any(e.data.get("plugin_name") == "widget" for e in collector.events_received), (
            "core.plugin_reloaded not delivered for widget"
        )

        await wait_until(lambda: len(error_events) >= 1)
        err = error_events[0]
        assert err.data["source"] == "lifecycle"
        assert err.data["phase"] == "on_stop"
        assert err.data["plugin_name"] == "widget"

    # ------------------------------------------------------------------
    # 5. No double on_stop via retained reference
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_no_double_on_stop_via_retained_reference(self, started_core):
        """Calling stop() on a retained old reference after reload must be a no-op.

        The kernel sets old._shutdown = True after the swap; Plugin.stop()
        guards on this flag and returns without calling on_stop again.
        """
        await started_core.load_plugin(_WIDGET_V1)
        v1 = await started_core.get_plugin("widget")

        await started_core.load_plugin(_WIDGET_V2)

        # v1.on_stop was called once during the reload swap.
        assert v1.on_stop_calls == 1

        # A stray stop() on the old reference must be a no-op.
        await v1.stop()
        assert v1.on_stop_calls == 1

    # ------------------------------------------------------------------
    # 6. Resource-leak regression: exactly one open resource after N reloads
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_resource_leak_regression_across_reload_cycles(self, started_core):
        """After N reload cycles, exactly one resource stays open — the live instance's.

        Pattern: on_start acquires a closeable stub resource (append to shared list),
        on_stop releases it (remove). After 5 reload cycles, exactly 1 resource is open.

        We drive reloads via core._reload_plugin_now() because load_plugin() exec-isolates
        its namespace and cannot share a Python list with inline-defined Plugin classes.
        _reload_plugin_now() exercises the identical kernel swap path (_swap_plugin).
        """
        open_resources: list[str] = []

        from uxok import Plugin as OrionPlugin

        class ResourcePlugin(OrionPlugin):
            """Plugin that tracks an open resource slot closed on on_stop."""

            _instance_count = 0

            def __init__(self, **kw):
                ResourcePlugin._instance_count += 1
                self._resource_id = f"res_{ResourcePlugin._instance_count}"
                super().__init__(name="resource_holder", **kw)

            async def on_start(self):
                open_resources.append(self._resource_id)

            async def on_stop(self):
                if self._resource_id in open_resources:
                    open_resources.remove(self._resource_id)

        # register_plugin starts the plugin internally (calls plugin.start()); no
        # manual start() call needed.
        first = ResourcePlugin()
        await started_core.register_plugin(first)

        # 5 reload cycles via the kernel's internal hot-reload entry point.
        # New instances must share the old ID (zero-downtime invariant), exactly
        # as load_plugin does: construct, then rebind identity via _assign_id.
        # Without this, swap_provider raises ValueError — the ID is the
        # discriminator for atomic swap.
        for _ in range(5):
            old_v = await started_core.get_plugin("resource_holder")
            new_v = ResourcePlugin()
            new_v._assign_id(old_v.metadata.id)
            await started_core._reload_plugin_now(old_v, new_v)

        # Exactly one resource must remain open — the live instance's.
        assert len(open_resources) == 1, (
            f"Expected 1 open resource, got {len(open_resources)}: {open_resources}"
        )
        live = await started_core.get_plugin("resource_holder")
        assert open_resources[0] == live._resource_id

        await started_core.stop()
