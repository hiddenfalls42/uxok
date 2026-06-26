# Secure capability access

`capability_access` is the kernel's access-posture setting for the capability system. It controls two things simultaneously: how much authority a plugin's `self.core` reference carries, and which capabilities a plugin is permitted to resolve at runtime. Three postures are available — `"open"` (the default), `"declared"`, and `"sealed"`.

## Why a posture axis exists

The three resolution policies (`capability_collision`, `capability_selection`, `capability_missing`) answer one question: given that multiple providers may exist for a capability, how should the kernel choose? They say nothing about whether a particular plugin is allowed to ask for a capability in the first place.

`capability_access` is that second, orthogonal question. It is a security axis, not a selection axis. A plugin may pass the grant check and still encounter the usual resolution policies; a plugin that fails the grant check never reaches the registry at all. The two dimensions compose but do not overlap.

In `"open"` mode every plugin can resolve every capability. This is the right default for development, for systems where every plugin is trusted equally, or when the simplest possible model matters more than access control. The mode is explicit rather than implicit: you can see the posture in `CoreConfig` and know exactly which guarantees are in play.

In larger systems, or whenever a plugin boundary is a trust boundary, that openness is too permissive. A plugin that provides image processing has no business registering new plugins or reaching a payment service it never declared. The `"declared"` and `"sealed"` postures encode that restriction in the plugin's manifest — what it says it needs is the ceiling of what it can touch.

## The three modes

**`"open"` (default).** No access control. Any plugin may resolve any capability. `self.core` inside every plugin is the real `Core` object with full lifecycle and graph-control authority. The three resolution policies apply to all resolutions as usual.

**`"declared"`.** Manifest-bounded access. A plugin may only resolve capabilities in its runtime grant — the union of `requires` and `resolves`. Anything outside that union is refused with `CapabilityAccessError` before any registry lookup. `self.core` is attenuated to a `CoreFacet` that exposes ambient kernel surfaces but omits lifecycle control and graph modification. Typed capability resolutions return the raw provider, as in `"open"`.

**`"sealed"`.** Manifest-bounded access with additional provider attenuation. Everything from `"declared"`, plus typed capability resolutions — calls that pass a `Protocol` type rather than a bare string — return a `CapabilityFacet` instead of the raw provider. Untyped string resolutions return the raw provider even under `"sealed"`. The `CapabilityFacet` is described below.

## Self.core attenuation

Under `"open"`, `self.core` inside a plugin is the real `Core`. It carries full authority: `register_plugin()`, `unregister_plugin()`, `load_plugin()`, `get_plugin()`, `start()`, and `stop()` are all reachable.

Under `"declared"` and `"sealed"`, the kernel attaches a `CoreFacet` to the plugin instead. The facet is an allow-listed view of the kernel — a thin object with no `__getattr__` passthrough, so missing a method raises `AttributeError` rather than silently proxying to the real kernel. The allow list comprises the ambient kernel surfaces a plugin legitimately needs:

- `events`, `hooks`, `config`, `tick`, `slip`, `state` — read-only ambient state
- `list()` — descriptive-only discovery; returns `PluginView` snapshots with no invocation path
- `check_plugin()` — the advisory admission probe; returns pure data with no mutation
- `get_capability()` — capability resolution, gated on the runtime grant

Graph control (`register_plugin`, `unregister_plugin`, `load_plugin`, `get_plugin`) and lifecycle control (`start`, `stop`) are absent. A plugin that needs graph control must explicitly request it through the `kernel.lifecycle` reserved grant, described below.

## The runtime grant: requires and resolves

Every plugin carries two sets that together form its runtime grant under `"declared"` and `"sealed"`:

`requires` is the load-order set. The kernel validates every entry at registration: if no live provider covers a `requires` name, registration fails with `MissingCapabilityError`. Because `requires` also functions as a runtime resolution grant, declaring a load-order dependency in `requires` is sufficient — no separate `resolves` entry is needed for a capability the plugin structurally depends on.

`resolves` is the runtime-only set. It extends the grant without adding a load-order constraint: a name in `resolves` needs no provider at registration time and is never checked structurally. This allows lazy, hot-loaded, or cycle-breaking resolutions — capabilities that may not exist when the declaring plugin registers, or that can appear and disappear during the system's lifetime.

The effective runtime grant is the union `requires | resolves`. Under `"declared"` or `"sealed"`, every `get_capability()` call checks the requested name against that union before touching the registry. A name outside the union raises `CapabilityAccessError` immediately.

There is one escape hatch: a plugin holding `"kernel.dispatch"` in its grant may resolve any capability by name regardless of its other declarations. This is for routing plugins and supervisors that cannot enumerate their dependencies statically.

## The sealed CapabilityFacet

Under `"sealed"`, a typed resolution — `await self.get_capability(SomeProtocol)` — does not return the raw provider. It returns a `CapabilityFacet`: a thin object that forwards only the protocol's declared public methods to the live provider and exposes nothing else. Attribute access for any name outside the protocol's method set raises `AttributeError`.

The facet is live-resolving. Each method call re-resolves the provider from the capability registry rather than holding a direct reference to the plugin instance. Two behaviors follow from this:

**Transparent rebind.** When the provider is swapped during a hot-reload, the facet picks up the new instance on the next method call. The consumer holding the facet does not need to re-resolve or even know a swap occurred.

**Revocation detection.** When the provider is unregistered with no replacement, the next method call raises `StalePluginError` rather than invoking a torn-down instance. The facet's liveness check catches this before any method is dispatched.

An untyped string resolution — `await self.get_capability("service_name")` — returns the raw provider even under `"sealed"`. A string call carries no protocol surface to attenuate to, so no facet is constructed for it.

## The return guard

A sealed capability method can in principle return a live plugin object or a kernel handle — a second-hop that bypasses the manifest's grant entirely. The `CapabilityFacet` checks each return value: if the result is a live `Plugin` instance or a kernel handle, the call raises `CapabilityAccessError` rather than returning the object.

This is one-hop accidental-leak protection. It catches the common case where a method incidentally returns `self` or hands back a plugin reference it received from somewhere else. It is not a security boundary.

The guard does not protect against:

- A method that encodes a handle inside a dict, a list, or a dataclass field
- A method that registers a callback and later invokes it with a handle as an argument
- Any indirection that carries the handle outside the single method-return check

If a real trust boundary is required between plugins, the return guard is not sufficient on its own. Within a single cooperative-async process, the kernel provides ergonomic guardrails — shared data structures, manifest checks, attenuated views — not a hard isolation wall. Process or thread isolation provides that guarantee.

## The two access-model errors

`CapabilityAccessError` is raised in two situations. First, when a plugin under `"declared"` or `"sealed"` calls `get_capability()` for a name not in its runtime grant — the call never reaches the registry. Second, by the sealed return guard when a capability method would hand back a live authority handle. Both situations signal the same class of problem: the plugin tried to reach authority outside what its manifest allows.

`StalePluginError` is raised by the `CapabilityFacet` when a method call re-resolves the provider and finds none — the capability was revoked after the facet was acquired. This is not an access violation; it is a lifecycle event. The appropriate response is either to re-resolve the capability, fall back to a degraded path, or propagate the error to a supervisor. Both exceptions are exported from the top-level `uxok` package.

## Reserved grants

Two capability names are reserved by the kernel and provided by no plugin:

`kernel.lifecycle` resolves to a `LifecycleFacet` that exposes exactly the four graph-control methods: `register_plugin()`, `unregister_plugin()`, `load_plugin()`, and `get_plugin()`. A plugin that declares `requires={"kernel.lifecycle"}` receives lifecycle authority without holding the full `Core`. Reserved grants are exempt from the missing-capability admission check — declaring `requires={"kernel.lifecycle"}` never fails registration. This is the sanctioned path for supervisors and loader plugins that need to control the graph.

`kernel.dispatch` is a grant, not a resolvable capability. A plugin that holds `"kernel.dispatch"` in its `requires | resolves` union may resolve any capability by name, bypassing the manifest check entirely. Use it only when the set of capabilities a plugin will resolve truly cannot be known at declaration time.

## Relationship to the resolution policies

The access posture and the three resolution policies are independent. The resolution policies govern the selection of a provider from the registry. The posture governs whether a given plugin is allowed to reach the registry for a given capability at all. They layer: a plugin under `"sealed"` that passes the grant check then encounters the same collision and selection policies as a plugin under `"open"`. Setting `capability_access` does not change how providers are chosen; it changes who is allowed to ask.

## Related pages

- [Lock down capability access](../how-to/how-to-lock-down-capability-access.md) — Set the mode, configure grants, and handle the access-model errors with working examples
- [Resolve a live plugin instance](../how-to/how-to-resolve-a-live-plugin-instance.md) — Use the `kernel.lifecycle` grant to act on a discovered plugin instance
- [Probe a plugin before admission](../how-to/how-to-probe-a-plugin-before-admission.md) — Use `check_plugin()` to validate a candidate against the live graph without committing
- [Capability system](capability-system.md) — The runtime dependency model, resolution policies, and tag-based filtering
- [Use capability policies](../how-to/how-to-use-capability-policies.md) — Configure the three resolution axes independently of the access posture
