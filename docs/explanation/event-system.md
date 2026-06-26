# Event system

The event system is one of the five kernel primitives in uxok. It implements publish-subscribe: plugins broadcast named events into the bus, and any number of other plugins receive them — without either side knowing the other exists.

## Why events exist

uxok's plugin model is designed so that any plugin can be added, removed, or replaced at runtime without touching others. Events are the mechanism that makes that possible. When a plugin publishes `cache.miss`, it is not calling any specific consumer. It is declaring that something happened. Every other plugin in the system can independently decide whether to react.

Think of events as signals on a shared wire. Any subscriber can listen to the wire without the emitter knowing how many listeners exist. Adding a new subscriber does not touch the emitter; removing one leaves the rest unaffected. That independence is what allows the system to grow by composition rather than coordination.

The alternative — direct calls between plugins — couples the caller to the callee's interface and lifecycle. Events break that dependency entirely.

## How dispatch works

When a plugin calls `await self.emit("some.event", data)`, the bus looks up every subscriber whose registered pattern matches the event name. It creates one independent asyncio task per subscriber and returns immediately. The emitter does not wait for any subscriber to finish.

Concurrency here is structural, not optional. Each subscriber runs as its own tracked task. A subscriber that takes 200 ms does not delay the emitter or any other subscriber receiving the same event. A subscriber that raises an exception does not prevent others from executing — the exception is caught, logged, and re-broadcast as `core.plugin_error` so supervision infrastructure can observe it.

The bus tracks all in-flight dispatch tasks. On shutdown, `drain()` cancels them and awaits their settlement, so no orphaned callbacks outlive the core.

## Event names and the verbatim rule

Event names use dot-separated segments as a namespace convention: `cache.miss`, `db.query.executed`, `user.session.started`. The convention is yours to define; the bus imposes no structure.

The emitter's name is **never** added to the event topic. `self.emit("status.changed", ...)` publishes exactly `"status.changed"`, whether the caller is `MonitorPlugin` or `HealthPlugin`. Two plugins publishing `status.changed` share the same topic; subscribers receive both. The bus is blind to who sent an event — that is the point.

To identify the sender, every event carries a `source` field. `Plugin.emit()` stamps it automatically with the emitting plugin's name. Subscribers can inspect `event.source` to distinguish origin without the origin being encoded into the topic itself:

```python
class MonitorPlugin(Plugin):
    @event("status.changed")
    async def handle_status(self, evt):
        if evt.source == "health_plugin":
            # React differently based on who emitted
            ...
```

`event.source` is metadata. It does not affect routing.

## The Event object

Every delivery carries an `Event` instance. The dataclass is frozen — handlers receive the same immutable object every subscriber sees.

```python
from uxok import Plugin, event

class RecorderPlugin(Plugin):
    @event("cache.miss")
    async def record(self, evt):
        print(evt.name)       # "cache.miss"
        print(evt.data)       # payload dict (or whatever was passed)
        print(evt.source)     # name of the emitting plugin
        print(evt.tick)       # tick counter when published (0 if tick system not running)
        print(evt.slip)       # tick boundary drift at publish time
        print(evt.timestamp)  # Unix timestamp of creation
```

The `tick` and `slip` fields are stamped by the core when the tick system is running. Before the tick system starts, both are `0`. They are relevant for plugins that reason about timing and scheduling.

The `data` field is untyped. That is intentional. Keeping the payload flexible allows event schemas to evolve without breaking subscribers that ignore new fields. The tradeoff is that publishers and subscribers must agree on payload structure through documentation.

## Subscription patterns

The `@event` decorator accepts either an exact name or a glob pattern. Pattern matching uses Python's `fnmatch` rules: `*` matches zero or more characters including dot separators, so `cache.*` matches `cache.miss` and also `cache.miss.deep`. `?` matches exactly one character.

```python
class AnalyticsPlugin(Plugin):
    @event("user.login")
    async def on_login(self, evt):
        # Exact match — only "user.login" triggers this
        record_login(evt.data)

    @event("cache.*")
    async def on_any_cache_event(self, evt):
        # Glob — receives "cache.miss", "cache.hit", "cache.miss.deep", etc.
        metrics.increment(evt.name)

    @event("db.query.?")
    async def on_short_query_events(self, evt):
        # "?" matches exactly one character — "db.query.x" but not "db.query.executed"
        ...
```

The bus maintains a subscriber cache keyed by the concrete published event name. When the bus resolves subscribers for a given publish — whether through an exact match, a wildcard pattern match, or both — it stores the result tuple under that concrete name. Subsequent publishes of the same name return the cached tuple directly without re-running the fnmatch scan. The cache is invalidated wholesale only when a wildcard subscription is added or removed, because a pattern change can affect every name the cache holds. Exact subscriptions invalidate only their own entry.

## Subscription cleanup

Subscriptions registered through `@event` or `Plugin.subscribe()` are bound to the plugin instance. When the plugin stops or is hot-reloaded, the bus removes all its subscriptions automatically. There is no manual teardown required.

Hot reload tracks subscriptions by instance identity, not by plugin ID. The old instance's subscriptions are removed and the new instance's are registered in the swap, so the event topic stays live across reloads without requiring subscribers to re-register.

## Demand-gated emission

`Plugin.emit()` checks for subscribers before allocating the `Event` object. If nobody is listening, the call returns without creating any objects or entering the bus. This is the demand gate.

For expensive payload construction, the gate is available as an explicit check:

```python
async def on_data_processed(self, result):
    if self.has_subscribers("analytics.result"):
        payload = build_expensive_payload(result)
        await self.emit("analytics.result", payload)
```

`has_subscribers()` is mute-aware: it returns `False` when the topic is suppressed, so demand-driven code naturally skips muted topics without any special handling.

## Deferred emission

Pass `at_tick` to schedule delivery at a future tick rather than immediately. The call returns at once; the event fires when the tick clock reaches the target:

```python
await self.emit("task.due", {"id": task_id}, at_tick=self.core.tick + 500)
```

The target tick must be strictly greater than `core.tick` at call time. Passing a tick in the past raises `ValueError` immediately — there is no silent no-op for a scheduling mistake.

Self-rescheduling is the natural pattern for recurring events:

```python
INTERVAL = 100

class HeartbeatPlugin(Plugin):
    async def on_start(self):
        await self.emit("heartbeat", {}, at_tick=self.core.tick + INTERVAL)

    @event("heartbeat")
    async def tick(self, evt):
        # Do periodic work
        await self.emit("heartbeat", {}, at_tick=self.core.tick + INTERVAL)
```

## What events trade away

Events give you decoupling, broadcast, and non-blocking execution. They trade away three things.

**No return values.** `emit()` is fire-and-forget. There is no mechanism for the publisher to collect results from subscribers. If you need results from multiple handlers — for instance, asking all plugins to validate a request — use hooks instead.

**Eventual side effects.** Because handlers run asynchronously, side effects from a subscriber are not complete when `emit()` returns. Code that follows `emit()` cannot assume a subscriber has acted yet. Systems built on events are eventually consistent within a tick, not immediately consistent.

**Implicit payload contracts.** The `data` field carries no enforced schema. Publishers and subscribers must agree on structure through documentation and defensive coding. A subscriber that assumes `evt.data["key"]` exists will raise `KeyError` if the publisher stops including it.

**Payloads carry data, not live handles.** Pass primitives, dataclasses, ids, or capability *names* — not `self`, not another plugin instance, not your `self.core`. A live reference placed on a broadcast channel is an authority edge that appears in no manifest and cannot be reviewed: any subscriber that receives it can invoke through it, outside its own declared grants. The kernel does not (and in in-process Python cannot cheaply) enforce this; it is a convention. The one intended exception is the framework's own `plugin.registered` hook, which hands subscribers the live `Plugin` that registered so they can act on it — an enumerable, documented exception, not a pattern to copy.

## Choosing events over hooks

Events and hooks solve different problems. Events are for notification: broadcasting that something happened to an open-ended set of consumers. Hooks are for coordination: invoking a well-defined set of handlers in priority order and collecting their results.

Use events when the publisher has no interest in what subscribers do, when multiple independent consumers should react, or when non-blocking execution is acceptable.

Use hooks when you need to gather results, when execution order matters, when errors should propagate back to the caller, or when you are building a processing pipeline with multiple stages.

Many plugins use both: a hook to coordinate the processing pipeline, events to broadcast the outcome.

## Related pages

- [How to publish events](../how-to/how-to-publish-events.md) — emit events and schedule deferred delivery
- [How to subscribe to events](../how-to/how-to-subscribe-to-events.md) — register handlers and use glob patterns
- [Hook system](hook-system.md) — ordered, result-returning coordination between plugins
- [Architecture overview](architecture-overview.md) — how events fit into the kernel alongside the other four primitives
