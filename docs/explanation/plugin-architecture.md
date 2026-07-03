# Plugin architecture

uxok is a microkernel. The kernel provides six primitives — the event bus, the hook system, the plugin registry, the capability system, the plugin base class, and the timing system — and then steps back. Every feature beyond those six is a plugin: a self-contained unit that declares what it needs, what it offers, and how it reacts to the world around it. The architecture described here is why that model works and what it costs.

## Why plugins, not modules

The conventional alternative to a plugin system is a module system: import a module, call its functions, be done. Module systems are simple and fast, but they couple the calling code to a specific implementation at import time. If the implementation changes, callers change. If you want two versions of a feature to coexist, you need explicit plumbing.

A plugin architecture inverts this relationship. Plugins declare capabilities by name, and consumers declare which capabilities they need. The framework resolves the names to instances at runtime. The consumer never imports the provider directly. This indirection is the price of entry — it is also what makes hot-loading, zero-downtime swaps, and dynamic capability provisioning possible. Those properties are not available in a module system at any price.

## How plugins connect to the kernel

A plugin connects to the kernel at three moments: registration, start, and stop.

**Registration** is the first contact. The kernel validates that every capability the plugin requires is already provided by a registered plugin. If any declared requirement is unmet, registration fails before any code runs. This front-loads configuration errors so they surface immediately rather than at the first runtime call.

The dependency graph is also kept acyclic at registration time. At initial registration this is passive — declaring a dependency on an unregistered peer fails immediately, so cycles cannot form. During hot reload, where an updated plugin can name already-registered peers, the registry actively checks for cycles before committing the new edges and rolls back automatically on rejection.

**Start** activates the plugin's framework integration. Handlers marked with `@hook` and `@event` were already discovered at instantiation — `__init__` runs a per-instance introspection scan that finds each decorated bound method on the new object. Start takes those pre-collected handlers and registers them with the hook system and the event bus respectively. Configuration is validated against the declared schema. Then `on_start()` is called, giving the plugin a chance to acquire resources, emit its initial events, or query the capability system for providers it depends on.

**Stop** is the reverse. `on_stop()` runs first, giving the plugin a chance to release resources. Then a unified drain unregisters the plugin's event subscriptions, then its hooks, then its capabilities, then cancels its scheduler entries and background tasks. This ordering is deliberate: a plugin remains capable of emitting events and calling hooks until the drain begins, so `on_stop()` has full access to the kernel primitives it might need for graceful shutdown.

## The base class is not an interface

`Plugin` is a convenience layer, not a protocol. The underlying protocol is `PluginProtocol`, which any class can satisfy. `Plugin` simply removes boilerplate that nearly every plugin would write anyway: core reference storage, metadata construction, name auto-detection, decorator discovery, config lookup, and the fire-and-forget emit path.

Name auto-detection is one concrete example. A class named `MetricsCollector` becomes `metrics_collector` — the framework converts CamelCase to snake_case. Explicitly passing `name="metrics_collector"` to the constructor produces the same result; the auto-detection is a convention, not a constraint.

The convenience methods (`emit`, `config`, `get_capability`, `hook`) each hide a small but real piece of complexity. `emit` stamps `Event.source` with the plugin's name so subscribers can identify the sender without encoding it in the event topic. `config` follows a defined fallback chain — plugin-scoped config, then schema defaults, then `CoreConfig` attributes, then the caller-supplied default. `get_capability` accepts either a string name or a `Protocol` type; the typed form returns a properly typed value for IDE autocomplete without changing runtime behavior. None of these methods need to be called through the base class, but using them is significantly less error-prone than hand-assembling the equivalent calls.

## How capabilities decouple plugins

Capabilities are string names bound at runtime to plugin instances. A plugin that provides storage declares `provides={"storage"}`. A plugin that needs storage declares `requires={"storage"}`. The capability system holds the binding; neither plugin holds a reference to the other.

This string-based coupling has a typed counterpart. A plugin can declare `provides={StorageProtocol}` where `StorageProtocol` is a Python `Protocol` class. The framework derives the capability name from the class name and stores the type alongside the string so that `get_capability(StorageProtocol)` returns a correctly typed object. The runtime behavior is identical; the type only exists to help the IDE and the type checker. String names and Protocol types are interchangeable from the framework's perspective.

Tags extend capability resolution without adding new indirection. When several plugins provide the same capability — say, two inference backends — consumers pass `tag="local"` or `tag="cloud"` to select among providers. Tags are declared at construction time and are immutable thereafter. They affect only selection, not validation; the framework does not enforce that any particular tag exists.

## Hot-loading and what it requires of plugins

Hot-loading is the ability to swap a running plugin instance for a new one without stopping the application. The atomic swap process that makes this safe has one important implication for plugin authors: in-memory state does not survive a reload automatically.

During a swap, the kernel creates the new instance, starts it (registering its handlers), transfers the old plugin's identity onto the new instance so capability and subscription bindings keyed by ID remain valid, then drains the old instance. At every step, at least one instance holds the capability.

The mechanism for state continuity across a swap is `get_state` and `restore_state`. The old instance's `get_state` is called before the drain; the returned dictionary is handed to the new instance's `restore_state` after start is complete. The default implementations return and ignore an empty dict, which means no state carries over unless the plugin explicitly overrides both methods. A plugin that manages a counter or a buffer needs to serialize those values in `get_state` and deserialize them in `restore_state`. The contract is plain dicts — data, not live objects, because the new instance may run different code that no longer shares type definitions with the old one. The same data-not-handles discipline applies to the broadcast channels: event payloads and hook arguments should carry primitives, ids, or capability names, never a live plugin or `self.core`. A live reference on a broadcast channel is an authority edge that no manifest records and no reviewer can see — the kernel cannot cheaply enforce this, so it is a convention (see the [event system](event-system.md) and [hook system](hook-system.md) explanations).

Background tasks have a simpler story. Any task created through `create_background_task` is tracked by the plugin's internal task manager and cancelled automatically when the plugin drains. The new instance recreates whatever tasks it needs in `on_start`.

## The lifecycle state machine

A plugin instance is one-shot. The state progression is: unstarted → started → stopped. Once stopped, the instance cannot be restarted. If the same plugin is needed again, a new instance is created.

This design is a consequence of how resource cleanup works. Cleanup drains all registrations, unsubscribes all handlers, and cancels all tasks. There is no safe way to re-register these in place without risking double-registration or partial state. A fresh instance starts from a clean slate; re-entry into an already-stopped object would require undoing cleanup that is not tracked in sufficient detail to reverse reliably.

The implication for application code is that the object holding a plugin reference should not assume the plugin is running without checking, and that plugin instances should not be stored in long-lived caches unless the cache is notified of reloads.

## Tradeoffs

The plugin architecture buys modularity, hot-loading, and early dependency validation. It costs indirection at every inter-plugin call, a learning curve around the lifecycle model, and the convention of not holding direct references between plugins.

The indirection is the most visible cost. Code that would otherwise read as `result = storage.save(data)` instead reads as `storage = await self.get_capability("storage"); result = await storage.save(data)`. The second form is more verbose and requires understanding the capability system, but it allows the underlying provider to change without touching the consumer.

Indirection is also the reason static analysis across plugin boundaries is harder than in a module system. Type checking within a single plugin is normal Python. Across plugin boundaries via string capability names, the type checker sees `Any` unless Protocol types are used. Preferring Protocol-typed capability declarations over string names is the pragmatic mitigation.

The lifecycle model carries its own complexity. Plugins must be instantiated with a core reference, registered, and started in the right order, and they must be stopped cleanly. This is more ceremonial than instantiating a plain class, but it is the mechanism that makes cleanup and hot-loading deterministic. The alternative — managing cleanup manually in application teardown code — is less reliable and does not compose with the hot-reload path at all.

For practical guidance on building plugins, see the [how-to guide for extending the plugin base class](../how-to/how-to-extend-plugin-base.md). For how the capability system resolves providers and handles conflicts, see [capability system](capability-system.md). For the design principles that motivated this architecture, see [framework philosophy](framework-philosophy.md).
