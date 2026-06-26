# Capability system

The capability system is uxok's runtime dependency layer. It gives plugins a way to depend on services without importing them directly — a named indirection that the kernel resolves at runtime rather than at import time.

## Why the indirection exists

Direct imports between plugins couple them at the module level. When Plugin A imports Plugin B, Python binds that dependency the moment the module loads. Changing B's location, splitting it, or swapping it for a different implementation forces a change in A. Hot-loading becomes structurally impossible, because import bindings do not move.

The capability system breaks this by separating *declaration* from *resolution*. A plugin declares that it provides `"storage"` and another declares that it requires `"storage"`. The kernel, not the importer, connects them at registration time. The consumer never holds a reference to a concrete class — it holds a string name, and the kernel fills that name with a live object when asked.

Think of it like a phone number rather than a mailing address. A phone number is a stable identifier that the telephone network resolves to wherever the recipient happens to be right now. Moving house doesn't break the identifier. Capabilities are the phone numbers of the plugin graph.

The practical consequence: when a plugin reloads, the kernel swaps the provider object behind the same capability name. Consumers call the new implementation without knowing a swap occurred.

## What plugins declare

Every plugin carries two optional sets in its metadata: `provides` and `requires`. Both are sets of string names, though a name can also be a Protocol type (described below).

`provides` is a claim: "I will answer to this name." `requires` is a precondition: "I need someone else to answer to this name before I start." The kernel validates preconditions at registration time — if a plugin requires `"database"` and no registered plugin provides it, registration fails with `MissingCapabilityError`. This fail-fast contract means a running system has no dangling dependencies; every `requires` entry points to an active provider.

The dependency graph built from these declarations also determines startup and shutdown order. Providers always start before their dependents, and dependents stop before their providers. A plugin's `on_start()` and `on_stop()` handlers can safely use any required capability, because lifecycle order guarantees the capability is up when the handler runs.

Under the `"declared"` and `"sealed"` access postures, `requires` also serves as a runtime resolution grant — a plugin may only call `get_capability()` for names it declared in `requires` or in the separate `resolves` field. See [Secure capability access](secure-capability-access.md) for the full access model.

## Typed capabilities and protocol validation

String-named capabilities carry no inherent interface contract. Two plugins can both claim to provide `"storage"` while offering entirely different method sets. Protocol types solve this.

When a plugin supplies a Python `Protocol` type in place of a bare string, the capability system performs structural validation at registration time — not `isinstance()`, but a method-by-method check that every protocol method is present on the provider and that signatures are compatible. A provider that passes validation is structurally guaranteed to satisfy the protocol, regardless of its class hierarchy.

The consumer side mirrors this: requesting a capability by Protocol type rather than by string triggers the same structural check on the way out. If the provider's interface drifts from the protocol between registration and resolution, the lookup raises `PluginError` with the exact method and signature mismatch. Type drift fails loudly rather than producing subtle runtime errors.

## The three policy axes

The capability system is configurable along three independent axes. All three live in `CoreConfig` and take effect for every capability uniformly.

**Collision policy** governs what happens when a second plugin registers the same capability name. `error_on_conflict` refuses the second registration outright — each name has exactly one provider, and ambiguity is a hard error. `first_wins` accepts the second registration silently but ignores it, leaving the first provider in place. `last_wins_with_warning` accepts the second and logs a warning, building a list of providers in registration order.

The default is `last_wins_with_warning`. This is deliberate: in development, re-registering a plugin after a code change should just work. In production, where you want strict single-provider semantics, set `error_on_conflict`.

**Selection policy** applies only when the collision policy has allowed a list of providers to accumulate. It decides which provider `get_capability()` returns. `first_registered` always returns the provider at the head of the list. `last_registered` always returns the most recently added. The default is `last_registered`, paired with the default `last_wins_with_warning` collision policy so that "last wins" is consistent end-to-end.

**Missing policy** controls the behavior when `get_capability()` is called for a name with no registered provider. `raise` signals a hard error — the capability is absent, and the caller should not proceed. `return_none` returns `None`, giving the caller an opportunity to degrade gracefully. The default is `raise`, because silently returning `None` for a required service usually causes harder-to-diagnose errors downstream.

These three axes compose independently. Production systems typically want `error_on_conflict`, `first_registered`, and `raise`. Development and hot-reload scenarios typically want `last_wins_with_warning`, `last_registered`, and `raise`.

## Tag-based filtering

Policies are global configuration — they apply the same rule to every capability. Tags are a per-lookup escape valve that operates *within* the provider list after the collision policy has finished.

Each plugin carries an optional `tags` set in its metadata. A call to `get_capability()` can supply a `tag` keyword argument to filter the provider list to only providers whose tags contain that string. The selection policy then picks one from the filtered set. Without a tag argument, the full provider list is visible to the selection policy.

Tags enable contextual provider selection without requiring separate capability names for each variant. A `"storage"` capability might have both a `"local"` provider and a `"remote"` provider registered simultaneously under `last_wins_with_warning`. A consumer that wants durable persistence asks for `tag="remote"`; one that wants in-process speed asks for `tag="local"`; one that wants whatever the selection policy prefers asks for neither. The capability name stays stable; tags route to the right instance.

Tags are immutable after plugin construction. They have no predefined vocabulary — any string is valid — which means convention within an application governs what tags mean. Tags also have no effect on `requires` validation; the kernel checks whether *any* provider covers a required capability name, regardless of tags.

## Relationship to other primitives

The capability system and the event bus solve different problems in the same plugin graph. Events are broadcast: one publisher, potentially many subscribers, no direct reply. Capabilities are point-to-point services: one caller, one selected provider, a return value. Use events to announce that something happened; use capabilities to ask another plugin to do something and return a result.

Hooks sit between the two. A hook is a named extension point where multiple plugins collaborate to transform a value or make a joint decision. Capabilities are direct service calls; hooks are collaborative pipelines. If "validate this request" requires multiple plugins to each contribute a verdict, a hook is the right primitive. If "execute this query" should land on exactly one database plugin, a capability is right.

The capability system feeds into hot reload as well. When a plugin reloads, the kernel calls `swap_provider()` on the capability system, replacing the old plugin instance in every provider list it occupied while preserving the plugin ID. Consumers that cached the provider between calls should re-resolve after a reload event to pick up the new instance; consumers that resolve on every call get the new instance automatically. Two events mark these transitions: `core.capability.revoked` (payload: `capability`, `old_provider_id`) fires when a provider is removed with no replacement, and `core.capability.rebound` (payload: `capability`, `old_provider_id`, `new_provider_id`) fires when a provider is swapped during hot-reload. See [Use hot reload](../how-to/how-to-use-hot-reload.md) for subscription examples.

## Secure bindings: the exokernel inheritance

A capability is a secure binding, the same primitive the MIT exokernel is built on. An exokernel's one job is to bind a resource to a principal, authorize that binding once, and then leave the fast path alone. uxok's capability system has the same shape. The kernel checks a `requires`/`resolves` grant at registration, records the name-to-provider edge, and every later `get_capability()` call rides that pre-authorized binding without re-checking. Authorize once, call cheaply.

Three exokernel ideas carry over intact. **Secure binding** is the grant itself: a plugin reaches a provider only through a name the kernel agreed it could resolve, never through an import it could have forged. **Visible revocation** is the lifecycle event pair — `core.capability.revoked` when a binding drops with no replacement, `core.capability.rebound` when hot reload swaps the object behind a live name — so a consumer is told its binding moved instead of finding out through a stale reference. **Downloaded policy** is the rule that mechanism stays in the kernel while choice moves above it: the three policy axes and tag filtering decide which provider answers a name, but they are configuration a host sets, not behavior the kernel fixes.

One exokernel property does not carry over, and the gap is the important part. A hardware exokernel binds resources between principals that distrust each other, and the MMU stops a program from reaching past its bindings. uxok has no MMU. Plugins share one process and one trust domain, so a binding governs *authority*, not *memory* — what a plugin may ask the kernel for, not what a determined plugin could reach by going around it. The `"declared"` and `"sealed"` access postures narrow the authority side, handing a plugin an attenuated view of the graph that refuses names outside its grant, which is the software analog of a weaker binding. They do not manufacture an address-space wall. See [Secure capability access](secure-capability-access.md) for that model and its limits.

## Design tradeoffs

Indirection is the central cost. Capability resolution adds a runtime lookup step that direct imports do not have. For very hot paths this may matter; for most plugin-to-plugin calls it does not. Caching the resolved provider in `on_start()` eliminates repeated lookup overhead for plugins that call the same capability frequently.

String-based names also defer type errors to runtime. A typo in a capability name surfaces at registration, not at the point of the call site. Protocol types close this gap for interface mismatches, but not for naming mistakes. Strict naming conventions and centralizing capability name strings in a shared module are the practical mitigations.

The gain is a dependency graph that the kernel can manipulate at runtime — reorder, replace, drain, and reconnect — without touching application code. This is what makes hot-loading structurally possible rather than a special case. The indirection is the mechanism by which the kernel retains control of the graph after construction.

## Related pages

- [Architecture overview](architecture-overview.md) — how the capability system fits into the five kernel primitives
- [Plugin architecture](plugin-architecture.md) — `provides`, `requires`, and plugin metadata in detail
- [Hook system](hook-system.md) — when collaborative extension points are more appropriate than service capabilities
- [Event system](event-system.md) — when broadcast communication is more appropriate than point-to-point capabilities
- [Framework philosophy](framework-philosophy.md) — the design principles behind the kernel-style dependency model
