# Getting started

Build your first uxok program. By the end you will have a running host that
registers two plugins, exercises the capability system and the event bus, and
shuts down cleanly — output you can see and verify.

## What you will build

A minimal host with two plugins:

- `CounterPlugin` — provides a `"counter"` capability (an object with an `increment()` method)
- `ReporterPlugin` — declares `requires={"counter"}`, fetches the provider on start, and emits an event

Running the program prints two lines to stdout. That is the test: if you see those
lines, every major primitive worked.

## Prerequisites

- Python 3.12 or higher
- Familiarity with `async`/`await` at the level of reading the standard library docs

## Install

```bash
pip install uxok
```

## The complete program

Create a file called `main.py`. The full source is below; the walkthrough after
it explains each part.

```python
import asyncio
from uxok import Core, Plugin, event, hook


class CounterPlugin(Plugin):
    """Provides a simple integer counter as a capability."""

    def __init__(self):
        super().__init__(provides={"counter"})
        self.count = 0

    def increment(self) -> int:
        self.count += 1
        return self.count

    @event("counter.reset")
    async def handle_reset(self, ev):
        self.count = 0
        print(f"Counter reset (source: {ev.source})")


class ReporterPlugin(Plugin):
    """Consumes the counter capability and reports on it."""

    def __init__(self):
        super().__init__(requires={"counter"})

    async def on_start(self):
        counter = await self.get_capability("counter")
        value = counter.increment()
        print(f"Counter after increment: {value}")
        await self.emit("counter.reset")


async def main():
    core = Core()

    await core.start()
    await core.register_plugin(CounterPlugin())
    await core.register_plugin(ReporterPlugin())

    await core.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

Run it:

```bash
python main.py
```

Expected output:

```text
Counter after increment: 1
Counter reset (source: reporter_plugin)
```

## Walkthrough

### The host

`Core` is the host. It owns the event bus, hook system, plugin registry, and
capability system. You create one instance and call `await core.start()` before
registering any plugins.

```python
core = Core()
await core.start()
```

`start()` transitions the core from `INITIALIZED` to `RUNNING` and starts the
internal tick clock. The constructor accepts optional keyword arguments for every
tunable — `max_plugins`, `tick_rate`, `plugin_configs`, and others — but the
defaults work for most programs and nothing is required.

### Plugins

Every plugin subclasses `Plugin`. All constructor arguments — `name`, `version`,
`provides`, `requires`, and others — are keyword-only. There is no `core`
parameter; the kernel attaches the core at registration time.

```python
class CounterPlugin(Plugin):
    def __init__(self):
        super().__init__(provides={"counter"})
        self.count = 0
```

The name `"counter_plugin"` is derived automatically by converting the class name
from CamelCase to snake_case (no suffix is stripped; `CounterPlugin` becomes
`counter_plugin`). Override it with `name="my_name"`.

`provides={"counter"}` declares this plugin as a provider of the `"counter"`
capability. Any plugin declaring `requires={"counter"}` can then call
`self.get_capability("counter")` to receive this instance.

### Capabilities

The capability system is uxok's dependency injection mechanism. It is explicit:
both sides declare their contract in `__init__`, and the kernel validates them at
registration time.

```python
class ReporterPlugin(Plugin):
    def __init__(self):
        super().__init__(requires={"counter"})

    async def on_start(self):
        counter = await self.get_capability("counter")
        value = counter.increment()
```

`get_capability("counter")` returns the plugin instance that provides that
capability. In this case that is the `CounterPlugin` instance itself — method
calls on it work directly.

Registration order matters. The kernel checks `requires` when `register_plugin` is
called and raises `MissingCapabilityError` immediately if no registered plugin
provides the named capability. Register providers before consumers.

### Events

`self.emit(name, data)` publishes an event. The name is published verbatim — no
prefix is added. `Event.source` is set automatically to the emitting plugin's name,
so handlers know who sent the event without encoding that in the topic.

```python
await self.emit("counter.reset")
```

`@event("counter.reset")` subscribes a method to that exact name. The decorator
accepts glob patterns too — `@event("counter.*")` matches any event whose name
begins with `counter.`, including multi-segment names like `counter.ops.reset`.

```python
@event("counter.reset")
async def handle_reset(self, ev):
    self.count = 0
    print(f"Counter reset (source: {ev.source})")
```

The handler receives an `Event` object. `ev.source` is the name of the plugin that
called `emit()` — here `"reporter_plugin"`, which matches the second line of
expected output.

Dispatch is concurrent fire-and-forget. `emit()` returns immediately; each
subscriber runs as its own independent asyncio task. A slow handler never blocks
the publisher or other subscribers.

### Shutdown

```python
await core.stop()
```

`stop()` calls `on_stop()` on every registered plugin, unregisters them all, and
transitions the core to `STOPPED`. The core is then reusable: call `start()` again
with a fresh plugin graph for the next run. Plugin instances are one-shot; do not
re-register a stopped plugin.

Override `on_stop()` to release resources your plugin acquired in `on_start()`:

```python
async def on_stop(self):
    await self.connection.close()
```

## Next steps

Each kernel primitive has its own how-to and explanation pages.

**Core and lifecycle**

- [Manage core lifecycle](../how-to/how-to-manage-core-lifecycle.md) — start, stop, state transitions
- [State management](../explanation/state-management.md) — the five-state machine, drain phase, restart flow

**Plugin**

- [Extend the Plugin base class](../how-to/how-to-extend-plugin-base.md) — constructor, metadata, lifecycle methods

**Events**

- [Publish events](../how-to/how-to-publish-events.md) — `emit()`, verbatim names, `Event.source`
- [Subscribe to events](../how-to/how-to-subscribe-to-events.md) — `@event`, glob patterns
- [Event system](../explanation/event-system.md) — pub/sub design, concurrent dispatch

**Hooks**

- [Register hook handlers](../how-to/how-to-register-hook-handlers.md) — `@hook`, `register_hook()`
- [Execute hooks](../how-to/how-to-execute-hooks.md) — `self.hook()`, priority ordering, `firstresult`
- [Hook system](../explanation/hook-system.md) — extension points, pipeline patterns

**Capabilities**

- [Work with capabilities](../how-to/how-to-work-with-capabilities.md) — `provides`, `requires`, `get_capability()`
- [Capability system](../explanation/capability-system.md) — dependency injection, provider selection

**Secondary**

- [Use plugin collections](../how-to/how-to-use-plugin-collections.md) — `core.list()`, `PluginCollection`, `PluginView`
- [Use hot reload](../how-to/how-to-use-hot-reload.md) — `core.load_plugin()`, state handoff
- [Declare plugin configuration](../how-to/how-to-declare-plugin-configuration.md) — `ConfigField`, `REQUIRED`, `self.config()`
