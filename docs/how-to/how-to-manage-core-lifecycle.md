# Manage core lifecycle

## Start the core

1. Create a `Core` instance and call `start()`.

```python
from uxok import Core

core = Core()
await core.start()
```

`start()` transitions the core from `INITIALIZED` to `RUNNING`. It starts the tick
clock and readies the event bus for dispatch.

## Register a plugin before starting

2. Skip the explicit `start()` call when you register a plugin immediately — the core
   auto-starts on the first successful registration.

```python
from uxok import Core, Plugin

core = Core()
await core.register_plugin(MyPlugin())
# core.state is now CoreState.RUNNING
```

Only the first registration triggers auto-start. Subsequent registrations on a running
core do nothing to core state.

## Use the context manager

3. Wrap the core in an `async with` block for automatic teardown.

```python
from uxok import Core

async with Core() as core:
    await core.register_plugin(plugin_a)
    await core.register_plugin(plugin_b)
    # work happens here
# core.stop() is called automatically on exit, even if an exception occurs
```

The context manager calls `start()` on entry and `stop()` on exit. Use it whenever the
core lifetime is bounded to a single code block.

## Stop the core

4. Call `stop()` to tear down the core and release all resources.

```python
await core.stop()
```

`stop()` is a full teardown. It transitions the core through `STOPPING` (the drain phase)
to `STOPPED`, unregistering every plugin in reverse dependency order — dependents are
stopped before the dependencies they rely on. After `stop()`, the registry is empty and
the core is reusable with a fresh plugin graph.

## Check the current state

5. Read `core.state` to inspect the current lifecycle position.

```python
from uxok.protocols import CoreState

match core.state:
    case CoreState.INITIALIZED:
        print("created, not yet running")
    case CoreState.RUNNING:
        print("operational")
    case CoreState.STOPPING:
        print("drain phase — teardown in progress")
    case CoreState.STOPPED:
        print("fully stopped, can restart")
    case CoreState.FAILED:
        print("teardown faulted — restartable via start()")
```

`CoreState` has five members: `INITIALIZED`, `RUNNING`, `STOPPING`, `STOPPED`, and
`FAILED`. Plugin errors do not change core state — they surface as `core.plugin_error`
events and are handled by supervisor plugins.

## Restart a stopped core

6. Call `start()` again from a stopped or failed core.

```python
await core.stop()
# core.state == CoreState.STOPPED

await core.start()
# core.state == CoreState.RUNNING
```

`start()` accepts both `STOPPED` and `FAILED` as starting points. It advances through
`INITIALIZED` and then to `RUNNING` in one call. Plugin instances are one-shot — use
`plugin.get_state()` / `plugin.restore_state()` to carry state across a restart.

!!! note
    `FAILED` means teardown itself faulted — not that a plugin failed. Plugin failures
    emit `core.plugin_error` events and leave the core `RUNNING`. A `FAILED` core is
    still restartable: `start()` re-initializes it and advances it to `RUNNING`, exactly
    as from `STOPPED`. If the faulted teardown may have left external resources in an
    unknown state and you cannot reason about what it left behind, constructing a fresh
    `Core` instance is the safer choice.

## Observe every state transition

7. Register a `core.state.changed` hook to react to lifecycle transitions.

```python
from uxok import Plugin, hook
from uxok.protocols import CoreState

class LifecycleMonitor(Plugin):
    @hook("core.state.changed")
    async def on_state_change(self, old: CoreState, new: CoreState) -> None:
        print(f"{old.value} → {new.value}")
        if new == CoreState.STOPPING:
            # flush buffers, close connections, etc.
            ...
```

The hook fires synchronously on every valid transition with the old and new states as
positional arguments.

## Tune the core at construction

8. Pass configuration as keyword arguments when creating the core.

```python
core = Core(
    max_plugins=50,
    tick_rate=100,          # Hz; lower for CPU-light workloads
    hook_precaching="disabled",
)
```

All options are fields of `CoreConfig`. Defaults work for most applications. See the
[`CoreConfig` reference](../reference/uxok/protocols/config.md) for the full field list.

See [state management](../explanation/state-management.md) for the complete transition
graph and the rationale behind the one-way teardown model.
