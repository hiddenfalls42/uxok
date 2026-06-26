"""RFC 0001 §3.3 — provider attenuation under capability_access="sealed".

A typed resolution returns a CapabilityFacet limited to the protocol surface; the facet
re-resolves the live provider per call, so it rebinds across a hot-swap and raises
StalePluginError after revoke (§3.4). Untyped resolutions return the raw provider.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pytest

from uxok import Core, Plugin
from uxok.core._capability_facet import CapabilityFacet
from uxok.errors import StalePluginError


@runtime_checkable
class Greeting(Protocol):
    async def hello(self, name: str = "World") -> str: ...


class Greeter(Plugin):
    """Provides 'greeting' (string) plus a non-protocol method `secret`."""

    def __init__(self, **kw):
        super().__init__(name="greeter", provides={"greeting"}, **kw)

    async def hello(self, name: str = "World") -> str:
        return f"hi:{name}"

    async def secret(self) -> str:
        return "leaked"


async def _started(mode: str) -> Core:
    core = Core(capability_access=mode)
    await core.start()
    return core


class TestSealedReturnsFacet:
    @pytest.mark.asyncio
    async def test_typed_resolution_returns_facet(self):
        core = await _started("sealed")
        try:
            await core.register_plugin(Greeter())
            g = await core.get_capability(Greeting)
            assert isinstance(g, CapabilityFacet)
            assert await g.hello("x") == "hi:x"
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_facet_hides_non_protocol_members(self):
        core = await _started("sealed")
        try:
            await core.register_plugin(Greeter())
            g = await core.get_capability(Greeting)
            # `secret` exists on the raw provider but is not on the protocol.
            with pytest.raises(AttributeError, match="secret"):
                _ = g.secret
            # non-protocol public names are attenuated.
            with pytest.raises(AttributeError):
                _ = g.nonexistent
            # private/dunder access is attenuated too (never forwarded).
            with pytest.raises(AttributeError):
                _ = g._private
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_untyped_resolution_returns_raw_under_sealed(self):
        """An untyped string resolution has no protocol surface — returns raw (Q#1)."""
        core = await _started("sealed")
        try:
            await core.register_plugin(Greeter())
            raw = await core.get_capability("greeting")
            assert not isinstance(raw, CapabilityFacet)
            assert await raw.secret() == "leaked"  # raw provider, full surface
        finally:
            await core.stop()


class TestOpenAndDeclaredReturnRaw:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["open", "declared"])
    async def test_typed_resolution_returns_raw(self, mode):
        core = await _started(mode)
        try:
            await core.register_plugin(Greeter())
            g = await core.get_capability(Greeting)
            assert not isinstance(g, CapabilityFacet)
            assert await g.secret() == "leaked"
        finally:
            await core.stop()


class SealedConsumer(Plugin):
    """Declares requires={Greeting} so it can resolve under declared/sealed."""

    def __init__(self, **kw):
        super().__init__(name="sealed_consumer", requires={Greeting}, **kw)

    async def via_self(self):
        return await self.get_capability(Greeting)

    async def via_core(self):
        return await self.core.get_capability(Greeting)


class TestSealedReachesBothPluginRoutes:
    """Regression: the protocol type must reach the sealed attenuation on BOTH the
    `self.get_capability` route and the `self.core.get_capability` (CoreFacet) route —
    the prior bug forwarded the derived string, which skipped the facet."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route", ["via_self", "via_core"])
    async def test_plugin_resolution_returns_facet(self, route):
        core = await _started("sealed")
        try:
            await core.register_plugin(Greeter())
            consumer = SealedConsumer()
            await core.register_plugin(consumer)
            g = await getattr(consumer, route)()
            assert isinstance(g, CapabilityFacet)
            assert await g.hello("x") == "hi:x"
            with pytest.raises(AttributeError, match="secret"):
                _ = g.secret
        finally:
            await core.stop()


class TestSealedRevocationBehavior:
    GREETER_V = """
class GreeterPlugin(Plugin):
    VERSION = {v}
    def __init__(self, **kw):
        super().__init__(name="greeter", provides={{"greeting"}}, **kw)
    async def hello(self, name="World"):
        return f"v{v}:{{name}}"
"""

    @pytest.mark.asyncio
    async def test_facet_rebinds_across_hot_swap(self):
        core = await _started("sealed")
        try:
            await core.load_plugin(self.GREETER_V.format(v=1))
            g = await core.get_capability(Greeting)
            assert await g.hello("x") == "v1:x"

            # Hot-reload to v2; the held facet must resolve the new provider.
            await core.load_plugin(self.GREETER_V.format(v=2))
            assert await g.hello("x") == "v2:x"
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_facet_raises_stale_after_revoke(self):
        core = await _started("sealed")
        try:
            await core.register_plugin(Greeter())
            g = await core.get_capability(Greeting)
            assert await g.hello("x") == "hi:x"

            await core.unregister_plugin("greeter")
            with pytest.raises(StalePluginError, match="greeting"):
                await g.hello("x")
        finally:
            await core.stop()
