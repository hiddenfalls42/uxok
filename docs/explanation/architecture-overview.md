# Architecture overview

uxok is a hot-loading plugin microkernel for Python. It provides exactly five primitives — event bus, hook system, plugin registry, capability system, and plugin base class — and nothing else. Every feature a real application needs is a plugin built on those primitives.

## Why a kernel, not a framework

Most plugin frameworks accumulate features in their core over time: built-in schedulers, logging adapters, HTTP clients, config parsers. Each addition is individually reasonable. Together they create a core that is hard to test, hard to understand, and difficult to upgrade without breaking downstream code.

uxok borrows a different model from operating systems. A kernel does not bundle device drivers. It provides only the primitives — process scheduling, system calls, module loading — that every driver needs. Drivers implement the rest. When a driver changes, the kernel does not. When a new class of hardware appears, a new driver appears alongside an unchanged kernel.

The same logic governs uxok. The core provides only the primitives every plugin needs. A plugin that provides database connectivity lives entirely outside the kernel. So does one that provides metrics, HTTP serving, or task scheduling. They can evolve, be replaced, or be absent without touching the kernel. The kernel itself does not change.

This is not an aesthetic preference. It is a structural guarantee: the kernel surface is small enough to verify completely, slow to change, and independent of any particular capability a user's application might need or not need.

## Microkernel or exokernel?

Both, on different axes. The two models answer different questions, and uxok takes its answer from each.

The kernel was designed with a mashup of concepts and the name `uxok` spells that hybrid in miniature: `u` for micro, `xo` for exo and `k` for kernel, referencing the three borrowed architectures. By structure uxok is a microkernel, but its capability system follows the MIT exokernel `xok`'s discipline — *mechanism, not policy*, resources reached through secure bindings, abstraction pushed out into plugins. It **stops short of the exokernel's hardware protection**: plugins share one process and one trust domain. 

A **microkernel** is a claim about structure: shrink the core to the primitives every service needs, and run everything else as separate services that talk over a message channel. uxok is a microkernel in exactly this sense. The five primitives are the core, every feature is a plugin, and the event bus is the channel between them. When uxok is called a microkernel, this is the accurate part.

An **exokernel** is a claim about resource discipline: provide *mechanism, not policy*, hand out resources through secure bindings that authorize once and cost little to use, and push the abstractions up into replaceable code above the kernel. uxok inherits this half directly. Capabilities are secure bindings — see [capability system](capability-system.md) — supervision and retry policy live in plugins rather than the core, and the kernel holds no opinion about what a plugin should be. The project takes its name from this side of the family.

Protection is where the exokernel analogy stops. A hardware exokernel multiplexes resources between programs that do not trust each other, and the MMU enforces the line. uxok runs every plugin in one process and one trust domain, with no boundary of that kind. It borrows the exokernel's binding discipline, visible revocation, and downloaded policy, and deliberately omits the protection guarantee: a plugin is constrained in what it may *ask the kernel for*, not in what it could reach by ignoring the kernel and touching another plugin's memory directly.

Microkernel by structure, exokernel by binding discipline, single trust domain by design.

## The five primitives

**Event bus.** The publish-subscribe channel between plugins. One plugin emits an event by name; every subscriber for that name receives it. Dispatch is concurrent — each subscriber runs as an independent async task and the emitter returns immediately. Events carry an immutable payload and a `source` field stamped with the emitting plugin's name, so subscribers can identify the origin without encoding it in the topic name.

**Hook system.** The request-response counterpart to events. A plugin calls a named hook and receives results back from every registered handler. Handlers execute in priority order — higher numbers first — so a plugin can position itself anywhere in a call chain. The `firstresult` option returns the first non-`None` result and stops, which is the correct shape for a capability-dispatch or validation gate.

**Plugin registry.** The identity and dependency ledger. The registry knows which plugins are loaded, what each one depends on, and the order in which they were loaded. Shutdown uses that order in reverse, so a plugin's dependencies are always still running when it is asked to stop.

**Capability system.** The typed dependency mechanism. A plugin declares what it `provides` and what it `requires`. Before a plugin starts, the kernel verifies every required capability is already provided by a running plugin and records the provider as a dependency. This transforms capability declarations into real dependency edges, which the registry enforces at shutdown. A plugin can also provide a Protocol type instead of a plain string; the kernel then validates the provider's method signatures against the protocol at registration time.

**Plugin base class.** The developer-experience layer. It handles name detection, metadata creation, decorator processing, config lookup, event emission, hook dispatch, and background task tracking. Plugin authors subclass it, declare their capabilities, and implement `on_start()` and `on_stop()`. The base class mediates all interaction with the kernel so authors rarely need to touch `Core` directly.

## How capabilities replace direct coupling

Without capabilities, one plugin that needs another must import it, hold a reference to it, and break if it is reloaded or replaced. Capabilities eliminate that coupling.

Think of capabilities the way USB works. A device does not care which specific USB controller is on the motherboard; it cares that a USB port exists and accepts the USB protocol. Swapping the controller does not break the device. Similarly, a plugin that declares `requires={"storage"}` does not care which plugin provides the `storage` capability, only that something does and that it satisfies the protocol. The provider can be reloaded, replaced with a faster implementation, or shadowed by a newer version — the consumer's code does not change.

The kernel enforces this at two points. At registration time it checks that every required capability is available and records the provider as a dependency. At shutdown it uses those edges to unregister plugins in safe order. Neither check is optional, because an unchecked dependency produces a failure at an arbitrary point during the program's lifetime rather than at the clean boundary of plugin startup.

## How hot reloading works

Hot reloading is loading a new version of a plugin — from a string, a file, a network source, anywhere — while the application is running and without interrupting anything that depends on it.

`core.load_plugin(code)` executes the code in an isolated module, discovers the `Plugin` subclass inside it, and checks whether a plugin with the same name is already registered. If it is, the kernel performs a zero-downtime swap: the new instance starts (registering its hooks and subscriptions), the registry atomically replaces the old instance with the new one under the same identity, capability registrations are reconciled, the old instance's state is handed to the new instance via `get_state()` / `restore_state()`, and then the old instance's resources are drained. The whole sequence is serialized by a reentrant lifecycle lock so concurrent calls cannot interleave.

The state handoff contract is intentional. `get_state()` returns a plain serializable dict from the old instance. `restore_state()` receives that dict on the new instance after it has already started. Keeping state as data — not live objects — means the new version is never holding a reference into old code, and the new version is free to migrate or discard fields the old version carried.

If the new plugin fails to start, the kernel rolls back: the old version keeps running without interruption. A failing reload is always safe.

## Lifecycle and state machine

`Core` itself moves through a small state machine: `INITIALIZED` → `RUNNING` → `STOPPING` → `STOPPED`. A restart is possible from `STOPPED` or `FAILED` by providing a fresh plugin graph — the kernel is reusable, not one-shot.

`STOPPING` is the drain phase. When `core.stop()` is called, in-flight event tasks and scheduled work are drained first. Then plugins are unregistered in reverse dependency order. `on_stop()` handlers run during this sequence, at a point when their dependencies are still available. After the last plugin is unregistered, the core transitions to `STOPPED` with an empty registry.

Plugin-level failures — a handler crash, a background task exception — do not change the core's state. They emit a `core.plugin_error` event. What to do with that event is a supervision policy decision, and supervision policy lives in a plugin, not in the kernel.

## Concurrency model

All kernel operations are async. The registry and capability system are mutated only in synchronous critical sections — no `await` appears inside a mutation — which makes each mutation atomic under cooperative asyncio without locks. The lifecycle lock serializes the multi-step operations that span awaits (register, load, unregister, swap). Per-plugin operation guards prevent the same plugin from being registered and unregistered simultaneously.

Event dispatch does not serialize. Each subscriber becomes an independent tracked task. A slow subscriber never blocks the publisher or other subscribers. Ordering within a single publish call is causal — a handler that emits a nested event completes before the handler continues — but there is no global ordering guarantee across independent publishers.

## What the kernel does not do

The kernel has no opinion about logging, metrics, HTTP, persistence, task scheduling, service discovery, authentication, or configuration formats. Those are capabilities. Providing them requires registering a plugin. Not needing them requires nothing — they are simply absent.

The kernel boundary is enforced in tests. Application code imports the kernel; the kernel never imports application code. That single-direction dependency is what makes the kernel stable: nothing outside it can introduce a requirement on it.

## Further reading

For how to build a plugin from scratch, see the [getting started tutorial](../tutorials/getting-started.md). For the design rationale specific to each primitive, see the sibling explanation pages: [capability system](capability-system.md), [event system](event-system.md), [hook system](hook-system.md), [plugin architecture](plugin-architecture.md), [state management](state-management.md), and [tick system](tick-system.md). For the API surface, see the [reference documentation](../reference/uxok/index.md).
