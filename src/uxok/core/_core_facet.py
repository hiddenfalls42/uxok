"""CoreFacet — the attenuated view of the kernel a plugin holds under
``capability_access="declared"`` / ``"sealed"`` (RFC 0001 §3.2.1-3.2.2).

Under ``"open"`` a plugin holds the real :class:`Core` (today's behavior). Under the
stricter modes the kernel attaches a ``CoreFacet`` instead, so the plugin's authority
over the kernel is bounded by its manifest. The facet exposes the **tier-1 ambient**
surface (including descriptive ``list`` and the read-only ``check_plugin`` admission
probe) and a **gated** ``get_capability``; it
deliberately omits the graph-control (tier-2: ``register_plugin``/``unregister_plugin``/
``load_plugin``/``get_plugin``) and host-only (tier-3: ``start``/``stop``) members, and
the kernel internals (``_capability_system``, ``_plugin_configs``, ``_tick_scheduler``).

Like ``PluginView``, this wrapper has **no ``__getattr__`` passthrough** — a passthrough
would re-expose everything and defeat the allow-list.

Not in ``uxok.__all__``: it is an implementation detail that quacks like the kernel
surface a plugin is allowed to touch, not a named public type.

Graph control is not on the facet itself; it is reached only through the tier-2
``kernel.lifecycle`` grant (:class:`LifecycleFacet`), resolved via ``get_capability``
when the plugin declares it in ``requires``.

Attenuated discovery (RFC 0001 §3.2.2, formerly open question Q3b): ``list()`` is
ambient on the facet and returns descriptive-only ``PluginView``s. The attenuation is
structural — the view's invocation members (``call``/``get_object``) were removed
kernel-wide, so enumeration cannot be a backdoor to invoking other plugins.

Attenuated admission (RFC 0006): ``check_plugin()`` is ambient for the same reason —
a pure read of graph state whose ``AdmissionResult`` is data, not handles. It discloses
no more than ``list()``, so a consumer (a gate, a loader) reaches the admission probe
without the ``_Plugin__core_real`` reflection escape and without a graph-mutation grant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from uxok.errors import CapabilityAccessError
from uxok.utils import derive_capability_name

if TYPE_CHECKING:
    # Concrete Core (not the protocol): the runtime object is always the real kernel,
    # and it carries members the protocol omits (e.g. `slip`).
    from uxok.core._core import Core
    from uxok.plugin._base import Plugin
    from uxok.protocols import AdmissionResult, CoreConfig, CoreState
    from uxok.protocols.events import EventBus
    from uxok.protocols.hooks import HookSystem
    from uxok.registry._plugin_proxy import PluginCollection


def enforce_requires(capability: str | type, owner: Plugin, mode: str) -> str:
    """Consumer-side secure binding (RFC 0001 §3.2, refined by RFC 0002): a plugin may
    resolve only the capabilities in its runtime grant — the union ``requires | resolves``
    — or, if it holds the reserved ``kernel.dispatch`` grant, any capability by name.

    ``requires`` is the load-order dependency set (validated at registration); ``resolves``
    is the runtime resolution allow-list (NOT validated at registration). Splitting them
    lets a plugin authorize lazy, cyclic, or hot-loaded resolutions that need not exist when
    it registers (RFC 0002 §2).

    A synchronous set-membership test — no ``await`` — so it preserves the lock-free
    capability-mutation invariant (decision record #12). Returns the resolved capability
    name. Raises :class:`CapabilityAccessError` when the mode enforces and the capability
    was not granted. ``"open"`` is a no-op.
    """
    name = derive_capability_name(capability) if isinstance(capability, type) else capability
    if mode == "open":
        return name
    grants = owner.metadata.requires | owner.metadata.resolves
    if name in grants or "kernel.dispatch" in grants:
        return name
    raise CapabilityAccessError(name, owner.metadata.name, sorted(grants))


class CoreFacet:
    """The plugin-safe, manifest-bounded view of the kernel (see module docstring)."""

    def __init__(self, core: Core, owner: Plugin) -> None:
        # Real Core held name-mangled-private; the owner plugin is the principal whose
        # `requires` grant gates capability resolution.
        self.__core = core
        self.__owner = owner
        # Resolve-once immutable tier-1 members as plain attributes (direct access, no
        # per-call indirection — RFC 0001 §3.2.3 efficiency note). These objects live as
        # long as the core; the bus/hook system are broadcast mechanisms, ambient by the
        # §2.3 scope decision (events/hooks are not enforced).
        self.events: EventBus = core.events
        self.hooks: HookSystem = core.hooks
        self.config: CoreConfig = core.config

    # Genuinely-changing reads stay thin properties.
    @property
    def tick(self) -> int:
        """Current tick number. Lock-free read."""
        return self.__core.tick

    @property
    def slip(self) -> int:
        """Current tick slip in periods. Lock-free read."""
        return self.__core.slip

    @property
    def state(self) -> CoreState:
        """Current core state (observing is benign)."""
        return self.__core.state

    async def get_capability(self, capability: str | type, *, tag: str | None = None) -> Any:
        """Resolve a capability — gated on the owner's ``requires`` declaration.

        This is the backstop for the ``self.core.get_capability(...)`` route;
        ``Plugin.get_capability`` enforces the same gate for the ergonomic
        ``self.get_capability(...)`` route. Both delegate to the unrestricted root
        ``Core.get_capability`` only after the gate passes.
        """
        enforce_requires(capability, self.__owner, self.config.capability_access)
        # Pass the original (possibly typed) capability through so sealed-mode attenuation
        # (Step 3) can see the protocol; the gate only needed the derived name.
        return await self.__core.get_capability(capability, tag=tag)

    async def list(self) -> PluginCollection:
        """Enumerate the plugin graph — **tier-1 ambient, attenuated** (RFC 0001 §3.2.2).

        "What exists" is benign, so this stays on the facet unconditionally. The
        returned ``PluginView``s are descriptive-only by construction — the invocation
        members (``call``/``get_object``) were removed from the view kernel-wide, so
        enumeration is not a backdoor to invoking other plugins. To *act on* a plugin,
        resolve it through the ``kernel.lifecycle`` grant or a typed capability.
        """
        return await self.__core.list()

    async def check_plugin(self, candidate: Plugin) -> AdmissionResult:
        """Advisory admission probe — **tier-1 ambient, attenuated** (RFC 0006 / spec 0005 §A).

        A pure read of graph state, the sibling of :meth:`list`: it asks "would this
        candidate fit what exists?" and mutates nothing. Its return is data, not handles
        (``AdmissionResult`` — name sets + bools), the canonical data-not-handles payload
        (RFC 0004 §E), so there is nothing to attenuate on the way out and no grant to
        require. It discloses no more than ``list`` already does, so it is ambient under
        every mode — a read-only kernel predicate reachable by a read-only path, not only
        by the ``_Plugin__core_real`` reflection escape. Forwards unchanged to the real
        ``Core`` (see :meth:`Core.check_plugin` for the verdict's scope boundary).
        """
        return await self.__core.check_plugin(candidate)


class LifecycleFacet:
    """Tier-2 ``kernel.lifecycle`` grant (RFC 0001 §3.2.2 / §2d).

    Graph control is deliberately absent from :class:`CoreFacet`; a plugin reaches it
    only by declaring ``kernel.lifecycle`` in ``requires`` and resolving it through
    ``get_capability``, which returns this forwarder. It exposes exactly the four
    graph-control methods and holds no other authority — the reserved capability the
    kernel "provides" with no plugin instance and no bootstrap ordering.

    Like the other facets it has no ``__getattr__`` passthrough and is not in
    ``uxok.__all__``. The forwarded methods return the **raw** Core results (live
    plugin instances): a granted tier-2 capability is full authority by design — the
    canonical holder, the supervisor, must restart real plugins.
    """

    def __init__(self, core: Core) -> None:
        self.__core = core

    async def register_plugin(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__core.register_plugin(*args, **kwargs)

    async def unregister_plugin(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__core.unregister_plugin(*args, **kwargs)

    async def load_plugin(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__core.load_plugin(*args, **kwargs)

    async def get_plugin(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__core.get_plugin(*args, **kwargs)
