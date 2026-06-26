# Use tick-based scheduling

Defer events and hooks to precise tick boundaries for deterministic, time-based coordination across plugins.

## Read the current tick

Access `self.core.tick` inside any plugin method to read the tick counter:

```python
from uxok import Plugin

class TimingPlugin(Plugin):
    async def on_start(self):
        current = self.core.tick
        print(f"Started at tick {current}")
```

`core.tick` returns `0` before `core.start()` and increments once the core is running.

## Schedule a deferred event

1. Compute a future tick number by adding an offset to the current tick.
2. Pass `at_tick=` to `self.emit()`. The call returns immediately — the event fires at the tick boundary, not now.

```python
from uxok import Plugin, event

class DeadlinePlugin(Plugin):
    async def schedule_deadline(self):
        target = self.core.tick + 5000   # 5 seconds at 1000 Hz
        await self.emit("task.due", {"task_id": 42}, at_tick=target)

    @event("task.due")
    async def handle_deadline(self, evt):
        print(f"Task due, fired at tick {self.core.tick}")
```

`at_tick` must be strictly greater than `core.tick` at the moment of the call. Passing a tick that is already in the past raises `ValueError` immediately:

```python
# Raises ValueError — never schedule at or before the current tick
await self.emit("too_late", {}, at_tick=self.core.tick - 1)
```

Always use `self.core.tick + N` to keep the target in the future.

## Schedule a deferred hook

1. Compute a future tick number.
2. Call `self.hook()` with `at_tick=`. Do **not** `await` it — `self.hook()` is not a coroutine and returns `None` when `at_tick` is set.

```python
from uxok import Plugin, hook

class ProcessingPlugin(Plugin):
    async def on_start(self):
        target = self.core.tick + 200
        self.hook("data.process", {"batch": "nightly"}, at_tick=target)

    @hook("data.process")
    async def process(self, data):
        print(f"Processing at tick {self.core.tick}: {data}")
```

The same `at_tick > core.tick` constraint applies. Passing a past tick raises `ValueError`.

## Implement recurring work

uxok has no `every_ticks` parameter. Implement recurrence by scheduling the next occurrence inside the handler itself:

```python
from uxok import Plugin, event

INTERVAL = 500  # ticks

class HeartbeatPlugin(Plugin):
    async def on_start(self):
        await self.emit("heartbeat", {}, at_tick=self.core.tick + INTERVAL)

    @event("heartbeat")
    async def handle_heartbeat(self, evt):
        print(f"Heartbeat at tick {self.core.tick}")
        # Schedule the next one before returning
        await self.emit("heartbeat", {}, at_tick=self.core.tick + INTERVAL)
```

Each handler schedules its own successor. The same pattern works with `self.hook()`.

## Coordinate phases across plugins

Schedule multiple events relative to a shared base tick so that all plugins see them in a known, fixed order:

```python
from uxok import Plugin

class CoordinatorPlugin(Plugin):
    async def coordinate_phases(self):
        base = self.core.tick
        await self.emit("phase.prepare", {}, at_tick=base + 100)
        await self.emit("phase.execute", {}, at_tick=base + 200)
        await self.emit("phase.commit",  {}, at_tick=base + 300)
```

Every plugin subscribed to these events receives them at the same tick boundaries, so ordering is consistent across the system regardless of subscription order.

## Read tick metadata from received events

Events published after `core.start()` carry tick metadata stamped by the event bus at actual publish time:

```python
from uxok import Plugin, event

class ObserverPlugin(Plugin):
    @event("task.due")
    async def track(self, evt):
        print(f"Fired at tick: {evt.tick}")    # Tick when actually published
        print(f"Slip:          {evt.slip}")    # Boundary drift in whole tick periods
        print(f"Source plugin: {evt.source}")  # Name of the emitting plugin
```

`evt.slip` is the number of whole tick periods the boundary drifted. See [tick system](../explanation/tick-system.md) for slip thresholds and the `core.tick_slip` event.

## Configure the tick rate

Pass `tick_rate` as a keyword argument to `Core` to change the clock frequency:

```python
from uxok import Core

core = Core(tick_rate=500)   # 500 Hz — 2 ms per tick
```

Pass multiple tick parameters at once when needed:

```python
from uxok import Core

core = Core(
    tick_rate=500,
    tick_slip_threshold=10,   # emit core.tick_slip only when slip >= 10 ticks
    tick_precision="hybrid",  # sleep + busy-wait for tighter boundaries
    tick_catchup="skip",      # skip missed ticks rather than replaying them
)
```

See [how to publish events](how-to-publish-events.md) for general event emission patterns and [how to execute hooks](how-to-execute-hooks.md) for hook calling conventions.
