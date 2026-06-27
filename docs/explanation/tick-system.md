# Tick system

The tick system is the kernel's clock. It gives every plugin a shared, monotonic
counter — `core.tick` — and a scheduling primitive — `at_tick` — for deferring
work to a precise future boundary. Everything else in the timing subsystem is
internal to the kernel.

The two public touchpoints are properties on `Core` and keyword arguments on
`Plugin.emit()` and `Plugin.hook()`. There are no tick-system classes in the
public API; `TickClock` and `TickScheduler` do not appear in `uxok.__all__`.

## What a tick is

A tick is one iteration of the kernel's internal clock loop. Think of it as the
heartbeat of the core: the loop fires at a fixed wall-clock rate, increments a
counter, dispatches any deferred work due at that boundary, and then sleeps until
the next boundary.

The counter is a monotonic integer. It never resets while the core is running and
never runs backward. `core.tick` reads it without acquiring a lock — asyncio's
single-threaded cooperative model makes the read safe from any async context
without awaiting.

`core.tick` returns `0` before `core.start()` is called. After that it climbs
without bound for as long as the core runs.

## Rate and precision

The default tick rate is 1000 Hz — one tick per millisecond. This gives scheduling
resolution of about 1 ms under a lightly loaded event loop. The rate is
configurable via `CoreConfig.tick_rate` up to a hard ceiling of 10,000 Hz.

```python
from uxok import Core

core = Core(tick_rate=500)  # 2 ms per tick
```

Two precision modes control how the clock waits between boundaries:

- `"sleep"` (default) — the clock uses `asyncio.sleep()` for the full wait
  interval. This yields the event loop on every tick, so all other coroutines get
  a chance to run.
- `"hybrid"` — the clock sleeps for most of the interval and then busy-waits for
  the last 200 µs (configurable via `tick_busy_wait_us`). This reduces jitter at
  the cost of holding the CPU. Use this only when sub-millisecond boundary
  accuracy matters more than CPU headroom.

## Tick slip

When the event loop is occupied — by a slow handler, a blocking call, or a CPU
spike — the clock wakes up later than its scheduled boundary. The number of full
tick periods the boundary was missed by is the **slip**. `core.slip` reads the
slip value for the most recently completed boundary.

A slip of `0` means the clock is on schedule. A slip of `3` means the loop woke
three full tick periods late — three milliseconds at the default rate.

When slip reaches or exceeds the `tick_slip_threshold` (default 5), the kernel
emits a `core.tick_slip` event. Subscribe to it to monitor timing health.

```python
from uxok import Core, Plugin, event

class TimingMonitor(Plugin):
    @event("core.tick_slip")
    async def on_slip(self, evt):
        print(f"tick {evt.data['tick']} slipped by {evt.data['slip']} periods")
```

The `core.tick_slip` event payload carries three keys:

| Key | Type | Description |
|---|---|---|
| `tick` | `int` | Tick number at which slip was detected |
| `slip` | `int` | Number of tick periods the boundary was late |
| `tick_rate` | `int` | Configured tick rate |

Adjusting `tick_slip_threshold` lets you tune the sensitivity. A threshold of `1`
catches any slip at all; `20` surfaces only severe stalls.

## How the clock handles missed boundaries

When the clock wakes up late it has two strategies, configured via
`CoreConfig.tick_catchup`:

`"skip"` (default) — the clock jumps forward to the current wall-clock boundary,
skipping every missed tick number in the gap. Deferred work that was due during
the gap fires once, late, rather than being replayed tick by tick. This keeps the
clock anchored to real time. It is the right choice for live systems where
wall-clock accuracy matters more than firing every tick.

`"burst"` — the clock replays each missed tick back-to-back before catching up.
This is for simulation and replay scenarios where every tick must be processed in
order, regardless of wall-clock drift.

## Deferring work with `at_tick`

`Plugin.emit()` and `Plugin.hook()` both accept an `at_tick` keyword argument.
When set, the call returns immediately — fire-and-forget — and the work is
scheduled for the named tick boundary. No result propagates to the caller.

```python
from uxok import Plugin, event

class Worker(Plugin):
    async def on_start(self):
        # Emit an event 100 ticks from now
        await self.emit("maintenance.run", {"phase": "cleanup"}, at_tick=self.core.tick + 100)

        # Execute a hook 50 ticks from now
        self.hook("data.flush", at_tick=self.core.tick + 50)
```

`at_tick` must be strictly greater than `core.tick` at the moment of the call.
Passing a tick that is already in the past, or equal to the current tick, raises
`ValueError` immediately — the failure is synchronous and eager so bugs are visible
before the tick boundary rather than silently lost.

When the scheduler fires deferred work it launches each entry as an independent
asyncio task. A factory that raises during coroutine construction is caught,
logged, and skipped; it does not propagate to the clock loop.

## Recurring work via self-rescheduling

There is no built-in periodic-execution primitive. Recurring work is expressed by
self-rescheduling: a handler fires at its target tick, does its work, and
schedules itself for the next interval before returning.

```python
from uxok import Plugin, event

class HeartbeatPlugin(Plugin):
    INTERVAL = 500  # ticks

    async def on_start(self):
        await self.emit("heartbeat.tick", {}, at_tick=self.core.tick + self.INTERVAL)

    @event("heartbeat.tick")
    async def on_heartbeat(self, evt):
        await self.check_health()
        # Re-arm for the next interval
        await self.emit("heartbeat.tick", {}, at_tick=self.core.tick + self.INTERVAL)
```

The `Sensor` in the reference `plugins/example_host/` uses exactly this pattern to emit
a reading every interval.

The same pattern works with `self.hook()`. The chain terminates naturally when the
plugin stops: pending `at_tick` entries belonging to the plugin instance are
cancelled during teardown, so no zombie work fires after unregistration.

## What the tick system is not

The tick system is not a task scheduler in the job-queue sense. It does not retry
failed work, track completion, or guarantee handler ordering. It advances a
counter and fires work on boundaries. Supervision, retry, and ordering logic
belong in plugins, not the kernel.

The tick system is also not the event dispatch model. Dispatch is concurrent
fire-and-forget: each subscriber runs as an independent asyncio task. There is no
serialization between subscribers within or across tick boundaries. The tick only
determines *when* deferred work is released into dispatch, not *how* it is
ordered afterward. See the [event system explanation](event-system.md) for how
dispatch works.

## Further reading

- [Event system](event-system.md) — concurrent fire-and-forget dispatch in detail
- [Hook system](hook-system.md) — hook execution and `hook(at_tick=)` deferral
- [State management](state-management.md) — how the tick clock relates to core lifecycle
- [API reference](../reference/uxok/protocols/core.md) — `core.tick` and `core.slip` properties
