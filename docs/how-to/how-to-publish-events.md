# Publish events

`Plugin.emit()` publishes an event. The name is delivered verbatim — no prefix is
added by the framework.

## Publish a basic event

1. Define your plugin class by subclassing `Plugin`.
2. Call `await self.emit(name, data)` wherever you want to signal an occurrence.

```python
from uxok import Plugin

class CounterPlugin(Plugin):
    async def on_start(self) -> None:
        await self.emit("counter.started", {"initial": 0})

    async def increment(self, value: int) -> None:
        await self.emit("counter.incremented", {"value": value})
```

Any subscriber registered to `"counter.incremented"` receives this event. The name
you pass to `emit()` is the name subscribers match against — exactly as written.

## Subscribe to an event in the same or another plugin

3. Decorate a method with `@event(name)` to receive the event.

```python
from uxok import Plugin, event

class LoggerPlugin(Plugin):
    @event("counter.incremented")
    async def handle(self, ev) -> None:
        print(f"value: {ev.data['value']}")
```

The `@event` decorator registers the handler during `Plugin.start()`. Cleanup
happens automatically when the plugin stops or reloads.

## Read the emitter's identity from Event.source

4. Access `ev.source` in the handler to identify which plugin sent the event.

```python
class LoggerPlugin(Plugin):
    @event("counter.incremented")
    async def handle(self, ev) -> None:
        print(f"from:  {ev.source}")      # "counter_plugin"
        print(f"value: {ev.data['value']}")
```

`Plugin.emit()` stamps `Event.source` with the emitting plugin's name. You do not
need to encode the sender into the topic. `Event.source` is `None` when an event is
published directly via `core.events.publish()` rather than through `Plugin.emit()`.

## Skip emission when nobody is listening

5. Check `self.has_subscribers(name)` before constructing an expensive payload.

```python
class MetricsPlugin(Plugin):
    async def report(self) -> None:
        if self.has_subscribers("metrics.snapshot"):
            payload = self._build_snapshot()   # only called when needed
            await self.emit("metrics.snapshot", payload)
```

`has_subscribers()` is mute-aware: it returns `False` when the topic is suppressed,
so the demand gate skips muted topics automatically. `Plugin.emit()` already
short-circuits internally when there are no subscribers, but the guard is useful
when payload construction itself is costly.

## Defer emission to a future tick

6. Pass `at_tick` to schedule the event for a specific tick boundary.

```python
class SchedulerPlugin(Plugin):
    async def queue_work(self) -> None:
        target = self.core.tick + 500
        await self.emit("work.due", {"task": "cleanup"}, at_tick=target)
```

`emit(at_tick=...)` returns immediately — the event is queued fire-and-forget.
`at_tick` must be strictly greater than `self.core.tick` at the moment of the call.
Passing a tick in the past raises `ValueError`.

```python
# Raises ValueError — tick is already past
await self.emit("work.due", {}, at_tick=self.core.tick - 10)
```

Always compute the target as `self.core.tick + N` to keep it in the future.

## Implement a recurring event

7. Schedule the next occurrence from inside the handler — there is no built-in
   recurrence primitive.

```python
INTERVAL = 100  # ticks

class HeartbeatPlugin(Plugin):
    async def on_start(self) -> None:
        await self.emit("heartbeat", {}, at_tick=self.core.tick + INTERVAL)

    @event("heartbeat")
    async def handle_heartbeat(self, ev) -> None:
        # periodic work here
        await self.emit("heartbeat", {}, at_tick=self.core.tick + INTERVAL)
```

## Read tick metadata from an event

Events published after the core starts carry timing context stamped by the bus.

```python
class ObserverPlugin(Plugin):
    @event("counter.incremented")
    async def observe(self, ev) -> None:
        print(f"name:   {ev.name}")       # "counter.incremented"
        print(f"source: {ev.source}")     # emitting plugin name, or None
        print(f"tick:   {ev.tick}")       # tick when published; 0 before core starts
        print(f"slip:   {ev.slip}")       # timing drift in tick periods; 0 = on-schedule
        print(f"data:   {ev.data}")
```

`tick` and `slip` are `0` when the tick system has not started. `timestamp` is
always present (set to wall-clock time at construction).

For how dispatch works and why the bus uses fire-and-forget concurrency, see
[Event system](../explanation/event-system.md).
