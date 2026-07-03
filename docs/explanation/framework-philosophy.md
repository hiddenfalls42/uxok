# Framework philosophy

uxok is a framework, not a product. That distinction is not a slogan — it is the load-bearing decision from which everything else follows. A framework supplies building blocks and leaves decisions to the builder; a product makes those decisions and enforces them. uxok supplies primitives (`Core`, `Plugin`, `event`, `hook`, `ConfigField`, the capability errors) and then steps aside.

## Why the kernel stays small

The public surface of `uxok` exports exactly twelve names. That number is deliberate restraint, not incompleteness. The event bus, the hook system, the plugin registry, the capability system, the base `Plugin` class, and the timing system are the irreducible minimum that every application needs. Every other feature — supervision, storage, metrics, tracing — is built on top of those primitives as a plugin, not embedded in the core.

The analogy is structural: uxok's relationship to its plugins resembles the Linux kernel's relationship to user space. The kernel provides syscall primitives; user space provides everything worth doing. uxok provides `Core` and `Plugin`; the ecosystem provides the application. The boundary matters because coupling works in one direction: plugins import the kernel, and the kernel never imports a plugin. Break that rule and the kernel acquires opinions it should not have.

Keeping the core small has a compounding benefit: stability. A primitive that only five things depend on is far easier to keep stable than a subsystem that three hundred things depend on. A small, frozen core is a bet that the right abstractions age well. uxok takes that bet.

## Convention over configuration

uxok's `Plugin` class auto-detects its name from the class name. You override it with `name=` when you need to, but most of the time you do not. That single convention removes a mandatory argument from every plugin definition ever written.

Conventions reduce the decisions that recur at every usage site. A configuration option answered once in a default is a decision you do not have to revisit. uxok extends this principle to scheduling: deferred work is expressed as `emit(at_tick=N)` rather than as a `DeferredEvent` subclass with a delay field and a drop policy. The convention covers most cases without introducing a class hierarchy. When a case truly needs more, that is a plugin.

The goal is not to eliminate configuration but to make it rare. When configuration does appear, all of it lives in one place: `CoreConfig`. Scattered configuration forces callers to understand which config class governs which setting. A single config class means a single place to look.

## Protocol-first design

uxok's primitives are defined as protocols, not as classes. The `EventBus` is a structural type — any object whose methods match the protocol is a valid event bus. `Core` depends on that protocol, not on any particular implementation. This matters because it makes every seam in the system testable in isolation. A test double that satisfies the protocol is all you need; no subclassing, no monkey-patching.

The protocol-first discipline also constrains future evolution. When a primitive is a protocol rather than a class, the implementation can change without touching the interface. Callers that depend on the protocol are unaffected. This is how uxok intends to hold backward compatibility: the protocols are the contract, and contracts do not break.

## User choice as a design value

The framework provides tools; users make decisions. uxok does not prescribe a supervision strategy, an error recovery policy, or a concurrency model. It exposes signals — `core.plugin_error`, `core.hook_error` — and then leaves the response to a supervisor plugin or to the application.

This is deliberate. The authors do not know your deployment topology, your failure budget, or your latency constraints. An opinionated framework that bakes in restart-on-failure and circuit breakers would be right for some users and wrong for others. A framework that provides the event bus and the hook system and then steps back is right for all of them, because each can implement the policy they need.

The same logic applies to feature adoption. New features are opt-in, enabled through configuration with sensible defaults. Existing code continues working when new capabilities are added. Backward compatibility is not a nice-to-have; it is the commitment that makes building on a framework safe.

## What gets rejected

Three categories of additions are consistently rejected, regardless of how useful they might seem in isolation.

Complex class hierarchies create coupling that is difficult to undo. When a feature requires subclassing a core type, it entangles the feature with core internals. uxok favors composition and protocols instead. If you need specialized behavior, write a plugin that composes the primitives.

Opinionated solutions assume a use case. A built-in chaos handling strategy assumes you want chaos handling and that uxok's chosen strategy fits your needs. Neither assumption is safe at the framework level. Provide the event bus; let users implement chaos handling as a plugin if they want it.

Breaking changes break trust. A framework that shifts its interfaces forces every downstream project to absorb the cost. uxok treats its public surface as a long-term commitment: additions are additive, and existing behavior is preserved. The `__all__` in `src/uxok/__init__.py` is the boundary that does not move.

## The decision framework

Every proposed addition to uxok passes through five questions. They are listed here not as steps but as a compact expression of the philosophy above.

Is this framework or product? Framework additions provide building blocks. Product additions prescribe solutions.

Does this add complexity? The default answer is yes, and the default verdict is no. The burden of proof is on the addition.

Is this opt-in? New behavior should not activate for existing callers. Defaults preserve the current behavior; new behavior is unlocked through configuration.

Does this break existing code? Backward compatibility is non-negotiable. If a change requires existing callers to update, it is the wrong change.

Is there a simpler way? Conventions beat configuration. Protocols beat inheritance. Simple data structures beat complex objects.

These questions do not guarantee good outcomes. But they make the framework's values explicit enough to catch drift before it accumulates.

---

## Further reading

- [Architecture overview](architecture-overview.md) — how the philosophy shapes the system's concrete structure
- [Getting started](../tutorials/getting-started.md) — the philosophy in practice, through a working plugin
