# Hook system

**Hooks** are serialized extension points — named call sites that any plugin can register handlers against, and any plugin can fire. When a caller fires a hook, the system runs every registered handler in priority order within the caller's own asyncio task, then returns their collected results. The caller waits for all handlers to complete before continuing.

That waiting is the point. Hooks exist for the cases where the event bus is the wrong tool.

## Why two communication primitives

The event bus and the hook system look similar from the outside — both are named channels that connect plugins — but they make opposite contracts.

When a plugin publishes an event, it is broadcasting. The emitter fires and continues immediately, unaware of who subscribed, what they did, or what they returned. Subscribers cannot send anything back. This asymmetry is not a limitation; it is the design. Events are for loose coupling: lifecycle notifications, status broadcasts, audit logging, anything where the emitter should be unaffected by how many listeners exist or how fast they run.

Hooks invert that contract. The caller fires a hook and waits. It receives a list of return values — one per handler — and can act on them. This is synchronous request-response communication across plugin boundaries. A validation hook, for example, lets multiple plugins each inspect data and return the errors they find. The caller aggregates those errors and decides whether to proceed. No version of the event bus can do this, because events have no return path.

The rule of thumb: use events when the emitter does not care what happens next; use hooks when the caller must influence or be influenced by the result.

## Global namespace and naming

Hook names live in a single flat namespace. No prefix is added automatically. `"data.validate"` is a hook named literally `data.validate`, accessible to any plugin in the system.

This global scope is a deliberate choice. It enables true cross-plugin coordination: a plugin in one part of the system can call an extension point defined by a completely unrelated plugin, and any number of other plugins can hook into that same point. If names were scoped per-plugin, this coordination would require explicit wiring. The tradeoff is that hook names must be chosen with care to avoid collisions — convention over configuration means using namespaced dot-separated names like `auth.login`, `cache.get`, or `pipeline.transform`.

## How handlers run

Handler execution runs directly in the caller's asyncio task, not in a separate coroutine or background job. This means the caller's execution context — its tick, its current position in the call stack — is shared with the handlers during the call. Handlers run serially, in priority order, one after the other. The next handler does not begin until the previous one completes or raises.

Priority is an integer, with higher values running earlier. Priority is about order, not selection. All registered handlers run; priority never filters a handler out.

Sorting happens once and is cached. The first call to a hook for a given name pays a small sorting cost; subsequent calls use the cached sorted list. Registration and unregistration invalidate the cache for that name. The execution snapshot is taken at the start of each call: if a handler registers or removes another handler during execution, that change takes effect on the next call, not the current one. This atomic-frame property means a running handler chain is stable.

When a handler raises, the exception is caught, logged with full context, and published as a `core.hook_error` event. The failing handler's slot returns `None`, and execution continues to the next handler. The caller receives the full result list with `None` in place of any failed result.

## What hook arguments and results carry

Hook arguments and return values carry data, not live handles. Pass primitives, dataclasses, ids, or capability *names* — not `self`, not another plugin instance, not your `self.core`. A hook is a broadcast call site: any plugin can register a handler against it and any plugin can fire it, so a live reference passed as an argument (or returned from a handler) becomes an authority edge that appears in no manifest and cannot be reviewed — a receiver can invoke through it, outside its own declared grants. The kernel does not (and in in-process Python cannot cheaply) enforce this; it is a convention, the same one the [event system](event-system.md) follows for payloads.

## The `firstresult` mode

The default call collects all handler results into a list. The `firstresult=True` mode short-circuits: it returns as soon as any handler returns a non-None value, without running the remaining handlers. This is useful when multiple plugins can satisfy a request but only one result is needed — a capability negotiation or a lookup, for example — and running every handler would be wasteful or incorrect.

## Deferred hook execution

A hook call can be deferred to a future tick with `at_tick=`. The call returns immediately — returning `None` rather than results — and schedules the hook execution for the specified tick. This is fire-and-forget: the result of a deferred hook call is unavailable to the caller. The tick value must be strictly in the future; specifying a past tick raises `ValueError` at call time.

For recurring execution, the pattern is self-reschedule: the handler itself schedules the next call from inside its body. There is no built-in repeat or interval mechanism; the handler controls its own cadence.

## Interaction with the lifecycle

Hooks registered via the `@hook` decorator are discovered at plugin initialization and registered with the hook system when the plugin starts. They are unregistered automatically when the plugin stops or is reloaded. During hot reload the kernel drains the old instance's handlers by instance identity before the new instance's handlers go live, so the hook chain is never in a state where both the old and new handler are registered simultaneously.

Handlers registered programmatically via `Plugin.register_hook()` — the method the `@hook` decorator desugars to — follow the same cleanup rules, because the registration binds the handler to the plugin instance's owner identity.

## Design tradeoffs

Hooks bring synchronous coupling: when plugin A calls a hook, it waits for plugin B's handler to finish. If B is slow, A is slow. If the hook has no handlers registered, the call returns an empty list — not an error. This is intentional; absent extension points are no-ops.

The coupling is also compositional. Hooks define open extension points, not point-to-point calls. A plugin calling `"data.validate"` has no compile-time dependency on the plugins that handle it. Handlers can be added, removed, or reordered at runtime without modifying the calling plugin. This is the mechanism by which the hook system enables extensibility: the caller defines the shape of the extension point; other plugins fill it.

Error isolation means a single broken handler cannot take down a hook chain. The `core.hook_error` event is the observable signal for monitoring and supervision. Callers that need to distinguish a missing result from a failed handler can check for `None` in the result list, or subscribe to `core.hook_error` events.

See the [event system explanation](event-system.md) for a deeper comparison of when to reach for each primitive, and the [plugin architecture explanation](plugin-architecture.md) for how the decorator and lifecycle fit together.

---

- [How to register hook handlers](../how-to/how-to-register-hook-handlers.md) — `@hook` decorator and `register_hook()` patterns
- [How to execute hooks](../how-to/how-to-execute-hooks.md) — calling extension points and collecting results
