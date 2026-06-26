# Explanation

These pages explain why uxok is built the way it is. Each one focuses on a single concept — the design decisions behind it, the tradeoffs it makes, and the mental model a plugin author needs to use it well. For step-by-step tasks, see [How-to](../how-to/index.md). For API signatures, see [Reference](../reference/uxok/index.md).

- [Architecture overview](architecture-overview.md) — The kernel model: five core primitives, the plugin layer that builds on top of them, and why the boundary between kernel and plugin is a hard line.
- [Plugin architecture](plugin-architecture.md) — How a plugin progresses from instantiation through registration, start, running, and stop; the base class, decorator system, metadata declarations, and hot-loading safety.
- [Capability system](capability-system.md) — How plugins declare what they provide and require, how the framework resolves those declarations at runtime, and how collision, selection, and missing-capability policies shape that resolution.
- [Event system](event-system.md) — The publish-subscribe bus: verbatim event names, `Event.source` for emitter identity, glob-pattern subscriptions, concurrent fire-and-forget dispatch, and `at_tick` deferred delivery.
- [Hook system](hook-system.md) — Priority-ordered, request-response coordination: how hooks differ from events, how handlers run serially in the caller's task, and when to reach for hooks instead of events.
- [Tick system](tick-system.md) — The monotonic tick counter (`core.tick`), tick slip detection, and the `at_tick=` scheduling primitive for deferring or recurring work.
- [State management](state-management.md) — The five-state constitutional machine (`INITIALIZED → RUNNING → STOPPING → STOPPED / FAILED`), valid transitions, the `STOPPING` drain phase, and why plugin failures are signals rather than states.
- [Framework philosophy](framework-philosophy.md) — The principles that govern every design decision: framework over product, simplicity over features, protocol-first design, and the decision framework used to evaluate changes.
- [Secure capability access](secure-capability-access.md) — The three access postures (open, declared, sealed), how `self.core` is attenuated under stricter modes, the sealed CapabilityFacet and its live-resolving behavior, and the return guard.
