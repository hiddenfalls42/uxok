# Subscribe to events

Use `@event` to bind a plugin method to an event name or glob pattern. The decorator
registers the handler automatically when the plugin starts.

## Subscribe to a specific event

1. Import `event` from `uxok` and `Event` from `uxok.protocols`.
2. Decorate any async method with `@event("event.name")`.
3. Accept one argument — the `Event` object — in the handler signature.

```python
from uxok import Plugin, event
from uxok.protocols import Event

class LoggerPlugin(Plugin):
    @event("counter.incremented")
    async def handle_counter(self, evt: Event) -> None:
        value = evt.data.get("value")
        print(f"Counter: {value}")
```

The handler receives the full `Event` object. Useful fields: `name`, `data`,
`source`, `tick`, and `slip`.

## Subscribe with a glob pattern

1. Pass a glob string to `@event` instead of a literal name.
2. Use `*` (zero or more characters) or `?` (exactly one character) as wildcards.

```python
from uxok import Plugin, event
from uxok.protocols import Event

class AuditPlugin(Plugin):
    @event("cache.*")
    async def on_cache_event(self, evt: Event) -> None:
        print(f"Cache event: {evt.name}")
```

Patterns use fnmatch syntax. `"user.*"` matches every event whose name starts with
`user.`. `"*.error"` matches any event whose name ends with `.error`.

## Attach multiple handlers

1. Decorate separate methods with `@event`, one per pattern.

```python
from uxok import Plugin, event
from uxok.protocols import Event

class MonitorPlugin(Plugin):
    @event("counter.*")
    async def on_counter(self, evt: Event) -> None:
        print(f"Counter event: {evt.name}")

    @event("cache.*")
    async def on_cache(self, evt: Event) -> None:
        print(f"Cache event: {evt.name}")
```

Each decorated method is registered independently. A single plugin can hold any
number of `@event` handlers.

## Identify the source of an event

1. Read `evt.source` in the handler body.

```python
from uxok import Plugin, event
from uxok.protocols import Event

class AuditPlugin(Plugin):
    @event("counter.incremented")
    async def audit(self, evt: Event) -> None:
        if evt.source == "counter_plugin":
            print(f"Trusted source, value: {evt.data.get('value')}")
        elif evt.source is None:
            print("Published directly via core.events.publish()")
        else:
            print(f"Other source: {evt.source}")
```

`evt.source` is the emitting plugin's name when the publisher used `plugin.emit()`.
It is `None` when the event was published directly via `core.events.publish()`.
Event names are always verbatim — no plugin prefix is added automatically.

## Subscribe dynamically at runtime

1. Override `on_start()` in your plugin.
2. Call `await self.subscribe(pattern, handler)` inside it.

```python
from uxok import Plugin
from uxok.protocols import Event

class DynamicPlugin(Plugin):
    async def on_start(self) -> None:
        await self.subscribe("cache.miss", self._on_miss)

    async def _on_miss(self, evt: Event) -> None:
        print(f"Cache miss: {evt.data.get('key')}")
```

`self.subscribe()` is what `@event` desugars to. Both paths clean up
subscriptions automatically when the plugin stops or is hot-reloaded.

## Offload heavy work to a background task

Event handlers run as concurrent asyncio tasks. Blocking work inside a handler
delays it and can starve other tasks.

1. Call `await self.create_background_task(coro)` to move long-running work off
   the handler.

```python
from uxok import Plugin, event
from uxok.protocols import Event

class WorkerPlugin(Plugin):
    @event("job.submitted")
    async def on_job(self, evt: Event) -> None:
        await self.create_background_task(self._process(evt.data))

    async def _process(self, data: dict) -> None:
        # Long-running work here — will not block other subscribers.
        ...
```

Background tasks are tracked and cancelled automatically when the plugin stops.

## Guard expensive payload building

Before constructing a costly payload, check whether anyone is listening.

```python
if self.has_subscribers("telemetry.snapshot"):
    await self.emit("telemetry.snapshot", build_snapshot())
```

`has_subscribers()` is mute-aware: it returns `False` when the event is suppressed,
so the payload is never built in that case.

See the [event system explanation](../explanation/event-system.md) for dispatch
behaviour, ordering guarantees, and design tradeoffs.
