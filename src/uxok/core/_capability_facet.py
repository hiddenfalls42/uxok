"""CapabilityFacet — provider-side attenuation under ``capability_access="sealed"``
(RFC 0001 §3.3).

A typed ``get_capability(SomeProtocol)`` returns a ``CapabilityFacet`` instead of the raw
provider: a thin object that forwards only the protocol's public methods to the **live**
provider and exposes nothing else. Because it re-resolves the provider from the capability
system on every call (the registry/capability table is authoritative, exactly like
``PluginView``), it inherits the §3.4 revocation behavior for free — a swap rebinds it
transparently to the new provider, and a revoke turns the next call into a
``StalePluginError`` rather than invoking a torn-down instance.

Typed only: an untyped string resolution has no protocol surface to attenuate to and
returns the raw provider even under ``"sealed"`` (RFC §3.3, open Q#1).

Not in ``uxok.__all__``: it is structurally the protocol (it satisfies the same type
via ``__getattr__`` forwarding) and is an implementation detail, not a named public type.
The method set is computed once from the protocol via the existing ``get_protocol_methods``
introspection — no new reflection.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from uxok.errors import StalePluginError
from uxok.utils import get_protocol_methods

if TYPE_CHECKING:
    from uxok.core._capability_system import CapabilitySystem


class CapabilityFacet:
    """Protocol-limited, live-resolving handle to a sealed capability provider."""

    def __init__(
        self,
        capability_system: CapabilitySystem,
        capability_name: str,
        protocol: type,
        tag: str | None = None,
    ) -> None:
        # All state is name-mangled so it never collides with — or is mistaken for —
        # a forwarded protocol method, and so __getattr__ (below) never sees these names.
        self.__sys = capability_system
        self.__name = capability_name
        self.__tag = tag
        self.__protocol_name = protocol.__name__
        self.__methods = frozenset(m["name"] for m in get_protocol_methods(protocol))

    def __getattr__(self, item: str) -> Any:
        # __getattr__ runs only when normal lookup fails, so the mangled attrs set in
        # __init__ never reach here. Expose only the protocol's public methods; anything
        # else (private members, non-protocol methods, stray dunders) is attenuated away.
        methods = self.__methods
        if item.startswith("_") or item not in methods:
            raise AttributeError(
                f"{self.__protocol_name} facet exposes only its protocol methods; "
                f"'{item}' is not one of {sorted(methods)}."
            )

        async def _forward(*args: Any, **kwargs: Any) -> Any:
            # Live re-resolution: the capability table is authoritative. A swap installs a
            # new provider (transparent rebind); a revoke leaves none (StalePluginError).
            provider = self.__sys._live_provider(self.__name, self.__tag)
            if provider is None:
                raise StalePluginError(
                    f"Capability '{self.__name}' ({self.__protocol_name}) has no live "
                    "provider; it was revoked after this handle was acquired."
                )
            result = getattr(provider, item)(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            # Return guard (RFC 0004 §4 / spec 0005 §C): a sealed method that hands
            # back a live Plugin or kernel handle is a manifest-invisible second-hop
            # leak — refuse it. Data/ambient/already-attenuated returns pass through.
            return self.__sys.attenuate_return(result, capability=self.__name)

        return _forward
