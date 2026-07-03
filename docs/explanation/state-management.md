# State management

The uxok core is a state machine. Every `Core` instance moves through exactly five states — INITIALIZED, RUNNING, STOPPING, STOPPED, FAILED — along a fixed set of allowed transitions. No transition outside that set is possible; attempting one raises `CoreError` immediately. This constraint is the foundation on which plugin lifecycle, capability availability, and teardown ordering all rest.

## Why a state machine

A plugin framework must answer two questions precisely: when is it safe for a plugin to use another plugin's capabilities, and when must a plugin release its own resources? Without a defined lifecycle, the answers are implicit — a plugin either guesses or the framework silently permits operations in states that cannot honour them.

The state machine makes the answers explicit. Each state carries a defined contract: what operations the core accepts, what subsystems are active, and what plugins may do. Transitions enforce those contracts at the boundary, not inside the operation. The result is that plugin code can trust the framework's promises without defensive checks.

There is no `ERROR` state. Plugin-level failures are signals — the `core.plugin_error` and `core.hook_error` events — not state changes. Supervision policy lives in plugins, not the kernel. `FAILED` is reached only when the teardown sequence itself faults, which is a distinct and rare condition.

## The five states

**INITIALIZED** is the state a `Core` enters at construction. The subsystems exist but no tick clock is running. Plugins may be registered; the core waits for an explicit `start()` call.

**RUNNING** is the operational state. The event bus, hook system, capability system, and tick clock are all active. Plugins handle events and hooks normally.

**STOPPING** is the drain phase. It begins the moment `stop()` is called on a running core. The core no longer accepts new lifecycle operations. Plugins stop in reverse dependency order — dependents before their dependencies — so that a plugin's `on_stop()` handler can still call into any capability it declared as a requirement. The phase ends when every plugin has stopped and every subsystem has released its resources.

**STOPPED** is the clean end state. All resources are released and all plugins are gone. The core instance is reusable: calling `start()` again moves it back to INITIALIZED and then to RUNNING.

**FAILED** marks a teardown that itself faulted. Like STOPPED, the core is reusable from this state: `start()` drives FAILED → INITIALIZED → RUNNING in a single call, re-registering a fresh plugin graph.

## The transition graph

```text
INITIALIZED → RUNNING
INITIALIZED → STOPPED
RUNNING     → STOPPING
STOPPING    → STOPPED
STOPPING    → FAILED
STOPPED     → INITIALIZED
FAILED      → INITIALIZED
```

Every transition is validated atomically under a lock. The state is updated before the `core.state.changed` hook fires; the hook fires outside the lock so that hook handlers may themselves call into the core without deadlocking.

INITIALIZED has two outbound edges by design. Calling `stop()` on a core that was never started takes it directly to STOPPED — a no-op teardown that completes cleanly. This lets code that holds a `Core` reference call `stop()` unconditionally without checking whether startup succeeded.

## The drain phase in detail

STOPPING exists because teardown is not a single atomic step. Stopping N plugins in reverse dependency order, draining in-flight event dispatch tasks, and clearing the capability system all take time. Without an explicit STOPPING state, these steps would happen either inside RUNNING (while the core still appears operational) or inside STOPPED (after the fact, when nothing could observe them).

STOPPING makes the drain visible. Any code that observes the `core.state.changed` hook knows when teardown has begun, can respond accordingly (flushing buffers, refusing new work, logging), and can distinguish between a core that is draining and one that has finished.

If any step during the drain raises an unhandled exception, the core transitions to FAILED rather than STOPPED. The FAILED state records that teardown did not complete cleanly, distinguishing that condition from a clean shutdown. Recovery is the same in both cases: call `start()` to reinitialize.

## Restart and reuse

A `Core` instance is reusable across any number of start/stop cycles. Each call to `start()` from STOPPED or FAILED drives the core through INITIALIZED into RUNNING. The plugin graph is empty at that point — `stop()` is a full teardown that unregisters every plugin. Plugin instances are one-shot; they do not persist across restarts.

State continuity across restarts is explicit and opt-in. A plugin that needs to carry data across a restart implements `get_state()` before stopping and `restore_state()` after the new instance starts. The framework does not preserve plugin state automatically, because implicit carryover between two plugin graphs is a source of subtle bugs.

## Observing state changes

The `core.state.changed` hook fires on every transition. It receives the previous state and the new state as arguments. This is the canonical way for a plugin to respond to lifecycle events — for example, flushing a write buffer when STOPPING begins, or starting a metrics reporter when RUNNING is reached.

```python
from uxok import Plugin, hook
from uxok.protocols import CoreState

class LifecycleObserver(Plugin):
    @hook("core.state.changed")
    async def on_state_change(self, old_state: CoreState, new_state: CoreState) -> None:
        if new_state == CoreState.STOPPING:
            await self.flush_pending_writes()
```

The hook fires after the state variable has been updated and before the transition method returns. A handler that reads `core.state` therefore sees the new state consistently.

State changes use a hook rather than an event for two reasons. First, hooks execute synchronously in priority order, so a high-priority handler can complete cleanup before lower-priority handlers run. Second, the hook mechanism predates RUNNING — it is wired at construction, not on start, so a plugin can observe the INITIALIZED → RUNNING transition even though it is registered before the core starts.

## What the state machine does not do

The state machine governs the core. It does not govern individual plugins. Each plugin has its own lifecycle (starting, running, stopping) that the core drives during core state transitions, but a plugin's own failure does not change the core's state. That separation keeps the kernel small: the core does not need to model degraded-plugin conditions, partial availability, or supervision hierarchies. Those concerns belong to a supervisor plugin or equivalent application-level code.

The transition graph is also fixed. It cannot be extended or overridden. If an application needs different lifecycle semantics — for example, a PAUSED state between RUNNING and STOPPING — the correct approach is to model that within a plugin or with a wrapper, not by modifying the core's state machine.

## Related pages

- [Architecture overview](architecture-overview.md) — how the state machine fits into the kernel's overall design.
- [Plugin architecture](plugin-architecture.md) — the plugin lifecycle and how `on_start()` and `on_stop()` relate to core state.
- [Hook system](hook-system.md) — how `core.state.changed` handlers are registered and executed.
