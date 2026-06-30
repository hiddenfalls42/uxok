"""RFC 0001 — secure capabilities: consumer-side binding + CoreFacet attenuation.

Covers `capability_access` enforcement (`"declared"`) and the attenuated `CoreFacet`
a plugin holds under the stricter modes. The provider-side `"sealed"` attenuation is
exercised separately (Step 3). `TestKernelLifecycleGrant` covers the reserved tier-2
`kernel.lifecycle` grant (RFC 0001 §2d) — the sanctioned path for a plugin that needs
graph control (e.g. a supervisor or loader).
"""

import pytest

from uxok import CapabilityAccessError, Core, Plugin
from uxok.core._core_facet import CoreFacet


class ProviderA(Plugin):
    def __init__(self, **kw):
        super().__init__(name="prov_a", provides={"cap_a"}, **kw)


class ProviderB(Plugin):
    def __init__(self, **kw):
        super().__init__(name="prov_b", provides={"cap_b"}, **kw)


class Consumer(Plugin):
    """Declares only cap_a; cap_b reach is undeclared."""

    def __init__(self, **kw):
        super().__init__(name="consumer", requires={"cap_a"}, **kw)

    async def resolve_declared(self):
        return await self.get_capability("cap_a")

    async def resolve_declared_via_core(self):
        return await self.core.get_capability("cap_a")

    async def resolve_undeclared(self):
        return await self.get_capability("cap_b")

    async def resolve_undeclared_via_core(self):
        return await self.core.get_capability("cap_b")


async def _build(mode: str) -> tuple[Core, ProviderA, ProviderB, Consumer]:
    core = Core(capability_access=mode)
    await core.start()
    a, b, c = ProviderA(), ProviderB(), Consumer()
    await core.register_plugin(a)
    await core.register_plugin(b)
    await core.register_plugin(c)
    return core, a, b, c


class TestConfigValidation:
    def test_invalid_capability_access_rejected(self):
        with pytest.raises(ValueError, match="capability_access"):
            Core(capability_access="nonsense")

    def test_valid_values_accepted(self):
        for mode in ("open", "declared", "sealed"):
            Core(capability_access=mode)

    def test_default_is_open(self):
        assert Core().config.capability_access == "open"


class TestOpenModeUnchanged:
    @pytest.mark.asyncio
    async def test_core_view_is_real_core(self):
        core, _a, _b, c = await _build("open")
        try:
            assert c.core is core  # not a facet
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_undeclared_resolution_allowed(self):
        """Under open, the requires gate is a no-op — any capability resolves."""
        core, _a, b, c = await _build("open")
        try:
            assert await c.resolve_undeclared() is b
        finally:
            await core.stop()


class TestDeclaredMode:
    @pytest.mark.asyncio
    async def test_core_view_is_facet(self):
        core, _a, _b, c = await _build("declared")
        try:
            assert isinstance(c.core, CoreFacet)
            assert c.core is not core
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_declared_capability_resolves(self):
        core, a, _b, c = await _build("declared")
        try:
            assert await c.resolve_declared() is a
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_undeclared_capability_raises(self):
        core, _a, _b, c = await _build("declared")
        try:
            with pytest.raises(CapabilityAccessError, match="cap_b"):
                await c.resolve_undeclared()
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_undeclared_via_core_route_also_raises(self):
        """The self.core.get_capability bypass route is gated identically."""
        core, _a, _b, c = await _build("declared")
        try:
            with pytest.raises(CapabilityAccessError, match="cap_b"):
                await c.resolve_undeclared_via_core()
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_declared_via_core_route_resolves(self):
        """A declared capability resolves through the facet's get_capability too."""
        core, a, _b, c = await _build("declared")
        try:
            assert await c.resolve_declared_via_core() is a
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_facet_omits_tier2_and_tier3_members(self):
        """Ambient authority is removed: graph control, host control, and kernel
        internals are not reachable through the plugin's view of the kernel."""
        core, _a, _b, c = await _build("declared")
        try:
            facet = c.core
            for forbidden in (
                "register_plugin",
                "unregister_plugin",
                "load_plugin",
                "get_plugin",
                "start",
                "stop",
                "_capability_system",
                "_plugin_configs",
                "_tick_scheduler",
            ):
                assert not hasattr(facet, forbidden), f"facet must not expose {forbidden}"
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_facet_exposes_tier1_ambient(self):
        core, _a, _b, c = await _build("declared")
        try:
            facet = c.core
            assert facet.config.capability_access == "declared"
            assert isinstance(facet.tick, int)
            assert isinstance(facet.slip, int)
            assert facet.events is core.events
            assert facet.hooks is core.hooks
            assert facet.state is core.state
        finally:
            await core.stop()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["declared", "sealed"])
    async def test_facet_list_is_ambient_and_descriptive_only(self, mode):
        """Discovery is ambient on the facet (RFC 0001 §3.2.2): a plugin can enumerate
        the graph under enforcement without any grant, and the views it gets back are
        descriptive-only — no backdoor to invoking another plugin."""
        from uxok.registry._plugin_view import PluginCollection

        core, _a, _b, c = await _build(mode)  # Consumer declares no kernel.lifecycle grant
        try:
            collection = await c.core.list()
            assert isinstance(collection, PluginCollection)
            # Descriptive fields are present...
            view = collection.by_name("prov_a")
            assert view is not None
            assert "cap_a" in view.provides
            # ...but the invocation members are gone — discovery is not a handle.
            assert not hasattr(view, "call")
            assert not hasattr(view, "get_object")
        finally:
            await core.stop()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["declared", "sealed"])
    async def test_facet_check_plugin_is_ambient(self, mode):
        """The admission probe is ambient on the facet (RFC 0006): a plugin holding the
        attenuated `CoreFacet` — with no `kernel.lifecycle` grant — can pre-flight a
        candidate via `self.core.check_plugin(...)` and get an `AdmissionResult`, instead
        of reaching past the facet to the un-attenuated real `Core`."""
        from uxok.protocols import AdmissionResult

        core, _a, _b, c = await _build(mode)  # Consumer declares no special grant
        try:
            facet = c.core
            assert isinstance(facet, CoreFacet)  # the call provably goes through the facet

            # A clean candidate (unique name, no requires) admits...
            clean = await facet.check_plugin(Plugin(name="prov_new"))
            assert isinstance(clean, AdmissionResult)
            assert clean.ok

            # ...a requires-miss surfaces in missing_requires...
            faulted = await facet.check_plugin(
                Plugin(name="needs_missing", requires={"absent_cap"})
            )
            assert not faulted.ok
            assert "absent_cap" in faulted.missing_requires

            # ...and the facet forwards unchanged — same verdict as the real Core (no drift).
            candidate = Plugin(name="prov_drift_check")
            assert await facet.check_plugin(candidate) == await core.check_plugin(candidate)

            # The probe mutated nothing: prov_new never entered the graph.
            assert (await core.list()).by_name("prov_new") is None
        finally:
            await core.stop()


class LifecycleConsumer(Plugin):
    """Declares the reserved kernel.lifecycle grant."""

    def __init__(self, **kw):
        super().__init__(name="lifecycle_consumer", requires={"kernel.lifecycle"}, **kw)


class TestKernelLifecycleGrant:
    """RFC 0001 §2d: kernel.lifecycle is a reserved, kernel-granted tier-2 capability."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["open", "declared", "sealed"])
    async def test_declaring_lifecycle_registers_without_missing(self, mode):
        """Declaring the reserved grant is always satisfiable — no provider needed."""
        core = Core(capability_access=mode)
        await core.start()
        try:
            await core.register_plugin(LifecycleConsumer())  # no MissingCapabilityError
        finally:
            await core.stop()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["open", "declared", "sealed"])
    async def test_resolves_to_lifecycle_facet(self, mode):
        from uxok.core._core_facet import LifecycleFacet

        core = Core(capability_access=mode)
        await core.start()
        try:
            consumer = LifecycleConsumer()
            await core.register_plugin(consumer)
            lc = await consumer.get_capability("kernel.lifecycle")
            assert isinstance(lc, LifecycleFacet)
            # Forwards graph control: it can see itself in the registry.
            assert await lc.get_plugin("lifecycle_consumer") is consumer
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_facet_forwards_load_plugin(self):
        """The grant forwards load_plugin to the kernel (hot-load through the facet)."""
        source = (
            "class Loaded(Plugin):\n"
            "    def __init__(self):\n"
            "        super().__init__(name='loaded')\n"
        )
        core = Core(capability_access="declared")
        await core.start()
        try:
            consumer = LifecycleConsumer()
            await core.register_plugin(consumer)
            lc = await consumer.get_capability("kernel.lifecycle")
            await lc.load_plugin(source)
            assert await lc.get_plugin("loaded") is not None
        finally:
            await core.stop()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route", ["self", "core"])
    async def test_undeclared_lifecycle_raises_under_declared(self, route):
        """A plugin that does not declare the grant cannot reach it under enforcement —
        gated identically on both the self.get_capability and self.core routes."""
        core, _a, _b, c = await _build("declared")  # Consumer requires only cap_a
        resolver = c.get_capability if route == "self" else c.core.get_capability
        try:
            with pytest.raises(CapabilityAccessError, match="kernel.lifecycle"):
                await resolver("kernel.lifecycle")
        finally:
            await core.stop()


class ResolverConsumer(Plugin):
    """RFC 0002: authorized to resolve cap_b at runtime via `resolves`, with no
    load-order tie — cap_b need not exist when this plugin registers."""

    def __init__(self, **kw):
        super().__init__(name="resolver_consumer", resolves={"cap_b"}, **kw)

    async def resolve_b(self):
        return await self.get_capability("cap_b")


class UnionConsumer(Plugin):
    """Declares cap_a as a load dependency and cap_b as a runtime-only grant; cap_c is
    in neither set."""

    def __init__(self, **kw):
        super().__init__(name="union_consumer", requires={"cap_a"}, resolves={"cap_b"}, **kw)

    async def resolve_a(self):
        return await self.get_capability("cap_a")

    async def resolve_b(self):
        return await self.get_capability("cap_b")

    async def resolve_c(self):
        return await self.get_capability("cap_c")


class DispatchConsumer(Plugin):
    """Holds the reserved kernel.dispatch grant — may resolve any name at runtime."""

    def __init__(self, **kw):
        super().__init__(name="dispatch_consumer", resolves={"kernel.dispatch"}, **kw)

    async def resolve_any(self, name: str):
        return await self.get_capability(name)


class TestResolutionGrants:
    """RFC 0002: `resolves` is the runtime resolution grant, split from the load-order
    `requires`; `enforce_requires` gates on the union; `kernel.dispatch` grants all."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["declared", "sealed"])
    async def test_resolves_not_validated_at_registration(self, mode):
        """A name in `resolves` with no provider does NOT block registration (unlike
        `requires`, which raises MissingCapabilityError)."""
        core = Core(capability_access=mode)
        await core.start()
        try:
            await core.register_plugin(ResolverConsumer())  # cap_b absent — must not raise
        finally:
            await core.stop()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["declared", "sealed"])
    async def test_late_resolution_after_provider_registers(self, mode):
        """The §2.3.1 case `requires` cannot express: resolve a provider registered
        AFTER the resolver."""
        core = Core(capability_access=mode)
        await core.start()
        try:
            consumer = ResolverConsumer()
            await core.register_plugin(consumer)  # registered before its provider
            b = ProviderB()
            await core.register_plugin(b)  # provider appears later
            assert await consumer.resolve_b() is b
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_union_grants_both_requires_and_resolves(self):
        core = Core(capability_access="declared")
        await core.start()
        try:
            a, b, uc = ProviderA(), ProviderB(), UnionConsumer()
            await core.register_plugin(a)
            await core.register_plugin(b)
            await core.register_plugin(uc)
            assert await uc.resolve_a() is a  # via requires
            assert await uc.resolve_b() is b  # via resolves
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_outside_union_raises_with_union_in_message(self):
        core = Core(capability_access="declared")
        await core.start()
        try:
            a, b, uc = ProviderA(), ProviderB(), UnionConsumer()
            await core.register_plugin(a)
            await core.register_plugin(b)
            await core.register_plugin(uc)
            with pytest.raises(CapabilityAccessError, match="cap_c") as ei:
                await uc.resolve_c()
            # The error reports the full runtime grant (the union), not just `requires`.
            assert "cap_a" in str(ei.value)
            assert "cap_b" in str(ei.value)
        finally:
            await core.stop()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["declared", "sealed"])
    async def test_kernel_dispatch_resolves_any_undeclared(self, mode):
        core = Core(capability_access=mode)
        await core.start()
        try:
            a, b, dc = ProviderA(), ProviderB(), DispatchConsumer()
            await core.register_plugin(a)
            await core.register_plugin(b)
            await core.register_plugin(dc)
            assert await dc.resolve_any("cap_a") is a
            assert await dc.resolve_any("cap_b") is b
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_kernel_dispatch_missing_provider_is_capability_error_not_access(self):
        """The grant authorizes the access; a missing provider still fails resolution —
        but with the ordinary CapabilityError, not CapabilityAccessError."""
        from uxok import CapabilityError

        core = Core(capability_access="declared")
        await core.start()
        try:
            dc = DispatchConsumer()
            await core.register_plugin(dc)
            with pytest.raises(CapabilityError) as ei:
                await dc.resolve_any("nonexistent_cap")
            assert not isinstance(ei.value, CapabilityAccessError)
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_resolves_irrelevant_under_open(self):
        """Under `"open"` the gate short-circuits — `resolves` neither helps nor is needed."""
        core = Core(capability_access="open")
        await core.start()
        try:
            b, consumer = ProviderB(), ResolverConsumer()
            await core.register_plugin(consumer)
            await core.register_plugin(b)
            assert await consumer.resolve_b() is b
        finally:
            await core.stop()


class TestErrorHierarchy:
    def test_capability_access_error_is_capability_error(self):
        from uxok.errors import CapabilityError

        assert issubclass(CapabilityAccessError, CapabilityError)

    def test_exported_from_package(self):
        import uxok

        assert "CapabilityAccessError" in uxok.__all__
        assert uxok.CapabilityAccessError is CapabilityAccessError
