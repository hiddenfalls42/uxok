"""RFC 0004 §4 / spec 0005 §C — sealed return guard.

Under ``capability_access="sealed"`` a typed resolution returns a CapabilityFacet;
the provider method behind it must not hand back a live authority handle. A
returned ``Plugin`` or kernel handle (``Core``/``CoreFacet``/``LifecycleFacet``) is
a manifest-invisible second-hop leak and is **refused (raised)**. Data, the ambient
bus/hooks, and already-attenuated views pass through. ``open``/``declared`` build no
facet, so the guard never runs there (regression).
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pytest

from uxok import CapabilityAccessError, Core, Plugin
from uxok.core._capability_facet import CapabilityFacet
from uxok.core._core_facet import CoreFacet, LifecycleFacet


@runtime_checkable
class Leaky(Protocol):
    async def give_self(self) -> object: ...
    async def give_core_facet(self) -> object: ...
    async def give_real_core(self) -> object: ...
    async def give_lifecycle(self) -> object: ...
    async def give_data(self) -> object: ...
    async def give_primitive(self) -> object: ...
    async def give_bus(self) -> object: ...
    async def give_hooks(self) -> object: ...
    async def give_collection(self) -> object: ...


@dataclass
class Payload:
    value: int


class LeakyProvider(Plugin):
    """Provides the Leaky protocol; each method returns a different thing."""

    def __init__(self, **kw):
        super().__init__(name="leaky_provider", provides={Leaky}, **kw)

    async def give_self(self):
        return self

    async def give_core_facet(self):
        return self.core  # a CoreFacet under sealed

    async def give_real_core(self):
        return self._Plugin__core_real  # the unattenuated Core

    async def give_lifecycle(self):
        return LifecycleFacet(self._Plugin__core_real)

    async def give_data(self):
        return Payload(value=7)

    async def give_primitive(self):
        return 42

    async def give_bus(self):
        return self.core.events

    async def give_hooks(self):
        return self.core.hooks

    async def give_collection(self):
        return await self.core.list()


async def _sealed_facet(mode: str = "sealed") -> tuple[Core, object]:
    core = Core(capability_access=mode)
    await core.start()
    await core.register_plugin(LeakyProvider())
    facet = await core.get_capability(Leaky)
    return core, facet


class TestRefusesAuthorityHandles:
    @pytest.mark.asyncio
    async def test_facet_is_built_under_sealed(self):
        core, facet = await _sealed_facet()
        try:
            assert isinstance(facet, CapabilityFacet)
        finally:
            await core.stop()

    @pytest.mark.parametrize(
        ("method", "leaked_type"),
        [
            ("give_self", "LeakyProvider"),
            ("give_core_facet", "CoreFacet"),
            ("give_real_core", "Core"),
            ("give_lifecycle", "LifecycleFacet"),
        ],
    )
    @pytest.mark.asyncio
    async def test_returned_handle_is_refused(self, method, leaked_type):
        core, facet = await _sealed_facet()
        try:
            with pytest.raises(CapabilityAccessError) as exc:
                await getattr(facet, method)()
            # the message names the leaked handle type
            assert leaked_type in str(exc.value)
        finally:
            await core.stop()


class TestPassesThroughSafeReturns:
    @pytest.mark.parametrize(
        "method",
        ["give_data", "give_primitive", "give_bus", "give_hooks", "give_collection"],
    )
    @pytest.mark.asyncio
    async def test_safe_return_passes(self, method):
        core, facet = await _sealed_facet()
        try:
            result = await getattr(facet, method)()
            assert result is not None
            assert not isinstance(result, (Plugin, Core, CoreFacet, LifecycleFacet))
        finally:
            await core.stop()

    @pytest.mark.asyncio
    async def test_data_value_intact(self):
        core, facet = await _sealed_facet()
        try:
            payload = await facet.give_data()
            assert payload == Payload(value=7)
        finally:
            await core.stop()


class TestOtherModesUnaffected:
    """open/declared build no facet, so the guard never runs (regression)."""

    @pytest.mark.parametrize("mode", ["open", "declared"])
    @pytest.mark.asyncio
    async def test_no_facet_no_guard(self, mode):
        core, provider_or_facet = await _sealed_facet(mode)
        try:
            # typed resolution returns the raw provider, not a facet
            assert not isinstance(provider_or_facet, CapabilityFacet)
            # so a method returning `self` leaks the raw plugin without raising
            leaked = await provider_or_facet.give_self()
            assert isinstance(leaked, LeakyProvider)
        finally:
            await core.stop()
