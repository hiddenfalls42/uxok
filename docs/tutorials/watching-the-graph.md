# Watching the graph

Starting from the `chat/` package [Time and the tick](time-and-the-tick.md) left you
with, you will add two plugins that observe the plugin graph itself: `roster.py` mirrors
who's currently loaded, and `supervisor.py` watches for repeat failures and evicts the
offender — no restart, no manual intervention.

## What you will change

Three new modules, one small `host.py` edit:

- `roster.py` *(new)* — mirrors registrations, unregistrations, hot-reloads, and
  capability rebind/revoke as they happen; answers a `roster.report` hook with a live
  census
- `supervisor.py` *(new)* — counts `core.plugin_error` per plugin, and evicts one that
  crosses a threshold
- `flaky.py` *(new, tutorial-only)* — a plugin that fails on a schedule, purely so this
  tutorial has something deterministic to supervise
- `host.py` — one config entry for `supervisor`, and one printed line showing the
  roster's census before lingering

Running it prints tutorial 5's six lines, then the graph events roster and supervisor
produce as the package boots, hot-reloads persona, and evicts `flaky`:

```text
roster: + roster
roster: + supervisor
roster: + terse_model (provides llm)
roster: + watcher
roster: + agent
user:  hello there
agent: Cheerfully #1: you said 'hello there'.
user:  what's the weather like?
agent: Cheerfully #2: you said 'what's the weather like?'.
persona: Cheerfully #3:
persona: Grumpily #4:
...[hot-reloaded persona]...
roster: ~ persona hot-swapped
roster: 9 plugins live; capabilities: llm
conversation done — edit a plugin file and save to see it hot-reload live
(Ctrl-C to exit)
supervisor: evicting flaky after 3 errors
roster: - flaky
```

## Why the graph needs a mirror

Every stage so far reacted to *content*: a question arriving, a file's modification time
changing. Nothing has watched the registry itself — who's currently loaded, what
capabilities they provide, when that set changes. `core.list()` plus the
`plugin.registered`/`unregistered` hooks are how a plugin observes the graph, without the
kernel needing a bespoke "the plugin list changed" event: registration and
unregistration were already there, `roster.py` just listens.

## `roster.py`: mirroring the graph

```python
"""Roster — mirrors the live plugin graph as it changes.

Registration traffic arrives via the ``plugin.registered``/``unregistered`` hooks, swap
traffic via the ``core.plugin_reloaded`` and ``core.capability.*`` events.
``roster.report`` answers with a one-line summary from ``core.list()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from uxok import Plugin, event, hook

if TYPE_CHECKING:
    from uxok.protocols import Event as EventType


class Roster(Plugin):
    """Prints graph changes; answers the ``roster.report`` hook with a live summary."""

    def __init__(self) -> None:
        super().__init__(name="roster")
        self._names: dict[str, str] = {}  # plugin id -> name (ids outlive departures)

    async def on_start(self) -> None:
        for view in await self.core.list():
            self._names[view.id] = view.name
        await self.register_hook("plugin.registered", self._on_registered)
        await self.register_hook("plugin.unregistered", self._on_unregistered)

    async def _on_registered(self, plugin: Any) -> None:
        meta = plugin.metadata
        self._names[str(meta.id)] = meta.name
        provides = f" (provides {', '.join(sorted(meta.provides))})" if meta.provides else ""
        print(f"roster: + {meta.name}{provides}")

    async def _on_unregistered(self, plugin_id: Any) -> None:
        name = self._names.pop(str(plugin_id), str(plugin_id))
        print(f"roster: - {name}")

    @event("core.plugin_reloaded")
    async def _on_reloaded(self, ev: EventType) -> None:
        print(f"roster: ~ {ev.data['plugin_name']} hot-swapped")

    @event("core.capability.*")
    async def _on_capability_change(self, ev: EventType) -> None:
        change = ev.name.rsplit(".", 1)[-1]  # "rebound" | "revoked"
        print(f"roster: ~ capability {ev.data['capability']} {change}")

    @hook("roster.report")
    async def report(self) -> str:
        plugins = await self.core.list()
        capabilities = ", ".join(plugins.capabilities)
        return f"{plugins.count} plugins live; capabilities: {capabilities}"
```

Four separate signals, not one — and each is a different shape of graph change:

- `plugin.registered`/`unregistered` fire only when the kernel actually commits a
  registration or removal — never from the advisory admission probe `check_plugin`
  makes when a plugin wants to test compatibility without registering (that returns an
  `AdmissionResult` and mutates nothing).
- `core.plugin_reloaded` isn't an unregister followed by a register — it's its own
  event, precisely because a hot reload can carry state across the swap, which a
  teardown-then-recreate wouldn't imply.
- `core.capability.*` catches both `capability.rebound` (a hot reload transparently
  rebinding a capability to the new instance) and `capability.revoked` (a capability's
  last provider actually leaving).

`register_hook` here does the same job it did for `watcher.py` in the previous stage:
`on_start` names `"plugin.registered"`/`"plugin.unregistered"` as hooks to listen on,
dynamically, instead of declaring them with `@hook` at class-decoration time. Same
mechanism, applied to graph events instead of a self-rescheduling timer.

`core.list()` returns a `PluginCollection` — indexed `PluginView`s plus a few
conveniences (`.count`, `.capabilities`, `.capability`/`.hook`/`.event` filters).
`report()` uses two of those; `roster`'s own `_names` dict exists only because a
`PluginView` for a plugin that already left the registry can no longer be resolved —
`view.uptime()` or `view.methods()` on a stale view raises `StalePluginError` rather than
returning stale data. `roster` avoids the problem entirely by keeping its own small
id-to-name cache instead of holding onto views past their plugin's lifetime.

## Supervision is a policy, not a kernel behavior

`core.plugin_error` and `core.hook_error` are signals only — the kernel notices a
failure and publishes it, and does nothing else. No retry, no eviction, no built-in
policy. `supervisor.py` is the first plugin in this series to actually consume that
signal:

```python
"""Supervisor — consumes the kernel's error signals and evicts repeat offenders.

Counts ``core.plugin_error``/``core.hook_error`` per plugin; on the first failure,
defers a review with ``emit(at_tick=...)`` so a burst gets judged once, after it
settles. A plugin over ``max_errors`` by review time is evicted through the
``kernel.lifecycle`` facet.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from uxok import ConfigField, Plugin, event

if TYPE_CHECKING:
    from uxok.protocols import Event as EventType

logger = logging.getLogger(__name__)


class Supervisor(Plugin):
    """Counts ``core.plugin_error`` per plugin; evicts repeat offenders after review."""

    def __init__(self) -> None:
        super().__init__(
            name="supervisor",
            requires={"kernel.lifecycle"},
            events_published={"supervisor.review", "supervisor.evicted"},
            config_schema={
                "max_errors": ConfigField(int, 3, "errors per plugin before eviction"),
                "review_delay_ticks": ConfigField(int, 50, "ticks to wait before reviewing"),
            },
        )
        self._errors: dict[str, int] = {}
        self._under_review: set[str] = set()

    async def on_start(self) -> None:
        self._lifecycle = await self.get_capability("kernel.lifecycle")

    @event("core.plugin_error")
    async def _on_plugin_error(self, ev: EventType) -> None:
        plugin_id = ev.data["plugin_id"]
        self._errors[plugin_id] = self._errors.get(plugin_id, 0) + 1
        logger.warning(
            "supervisor: %s failed in %s (%s): %s",
            ev.data.get("plugin_name", plugin_id),
            ev.data["source"],
            ev.data["error_type"],
            ev.data["error"],
        )
        if plugin_id not in self._under_review:
            self._under_review.add(plugin_id)
            review_at = self.core.tick + self.config("review_delay_ticks")
            await self.emit("supervisor.review", {"plugin_id": plugin_id}, at_tick=review_at)

    @event("core.hook_error")
    async def _on_hook_error(self, ev: EventType) -> None:
        logger.warning(
            "supervisor: hook %r failed in %s: %s",
            ev.data["hook_name"],
            ev.data["plugin_id"],
            ev.data["error"],
        )

    @event("supervisor.review")
    async def _review(self, ev: EventType) -> None:
        plugin_id = ev.data["plugin_id"]
        self._under_review.discard(plugin_id)
        count = self._errors.get(plugin_id, 0)
        if count < self.config("max_errors"):
            return
        offender = await self._lifecycle.get_plugin(plugin_id)
        if offender is None:
            return
        name = offender.metadata.name
        self._errors.pop(plugin_id, None)
        print(f"supervisor: evicting {name} after {count} errors")
        await self._lifecycle.unregister_plugin(plugin_id, force=True)
        await self.emit("supervisor.evicted", {"plugin_id": plugin_id, "plugin_name": name})
```

Only `core.plugin_error` feeds eviction — `_on_hook_error` just logs. That asymmetry is
deliberate: a raising hook callback is isolated *per call* (`execute()` treats it as a
`None` result and moves on to the next callback), so one bad hook invocation doesn't
necessarily mean the plugin itself is unhealthy the way a crashed background task or a
raising event handler does. `core.plugin_error`, by contrast, is exactly the signal that
something the plugin owns broke.

The deferred, debounced review (`emit(..., at_tick=...)`) is the same primitive
`watcher.py` used to reschedule its own scan, applied to a new purpose: instead of
"check again in N ticks regardless," it's "judge this specific burst once, N ticks after
it started" — a burst of five failures inside that window only schedules one review, not
five.

## `flaky.py`, named honestly

```python
"""Flaky — a deliberately broken plugin, purely to give this tutorial something to
supervise. Fires ``flaky.trip`` on a self-rescheduling hook; its own subscriber always
raises, so ``core.plugin_error`` shows up on a predictable schedule. Not a pattern to
copy — real plugins don't manufacture their own failures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from uxok import Plugin, event

if TYPE_CHECKING:
    from uxok.protocols import Event

_INTERVAL_TICKS = 2


class Flaky(Plugin):
    """Emits ``flaky.trip`` every few ticks; its own handler always raises."""

    def __init__(self) -> None:
        super().__init__(name="flaky", events_published={"flaky.trip"})

    async def on_start(self) -> None:
        await self.register_hook("flaky.tick", self._tick)
        self.hook("flaky.tick", at_tick=self.core.tick + _INTERVAL_TICKS)

    async def _tick(self) -> None:
        await self.emit("flaky.trip", {})
        self.hook("flaky.tick", at_tick=self.core.tick + _INTERVAL_TICKS)

    @event("flaky.trip")
    async def _trip(self, _ev: Event) -> None:
        raise RuntimeError("flaky: deliberate failure")
```

`supervisor.py` and `roster.py` are both already generic — copied near-verbatim from
`example_host/`. `flaky.py` is not: it exists only so this tutorial has a deterministic
failure to observe, in the same script run, without you having to time a live edit
against a review window. It's not something you'd write for a real plugin.

## `host.py`: a low threshold, and a look at the graph

```python
def host_configs() -> dict[str, dict[str, object]]:
    """Per-plugin configuration, validated against each plugin's ``config_schema``."""
    return {
        "agent": {"model_tag": "prose"},  # which llm provider the agent asks for
        "watcher": {"watch_dir": str(_HERE)},  # REQUIRED — no default exists
        "supervisor": {"max_errors": 2, "review_delay_ticks": 5},  # low so the demo is quick
    }
```

Production code would leave `supervisor`'s config at its defaults (`max_errors=3`,
`review_delay_ticks=50`); this tutorial turns both down so eviction happens within the
same short run instead of after fifty ticks.

The other change prints the roster's census once, right before the linger message:

```python
        report = await core.hooks.execute("roster.report", firstresult=True)
        print(f"roster: {report}")
        print("conversation done — edit a plugin file and save to see it hot-reload live")
```

`core.hooks.execute(name, firstresult=True)` is the same call `self.hook(name, ...)`
makes from inside a plugin — `host.py` isn't a plugin, so it goes through `core.hooks`
directly. No new API: it's the hook system's public entry point, used here from outside
a plugin instead of inside one.

## Run it

```bash
python -m chat.host
```

The first six lines are unchanged from tutorial 5. Watch for the roster's registration
lines as the package boots, the census line right before the linger invitation, and —
within a few ticks — `supervisor: evicting flaky after 3 errors` followed by
`roster: - flaky`.

Try it yourself: while the process is lingering, copy `flaky.py` to `flaky2.py` and
rename the class and `name=` inside it (anything distinct works). The watcher picks it
up as a new file, `roster: + flaky2` prints as soon as it registers, and a few ticks
later `supervisor` evicts it too — the same policy, applied to a plugin that didn't
exist when the process started.

## The key idea

The registry is queryable and observable through the same hook/event primitives used
everywhere else in this series — `core.list()`, a couple of well-named hooks, a couple
of well-named events, no new subsystem. Supervision is downloaded policy built entirely
from those primitives: the kernel publishes `core.plugin_error` and stops there;
deciding what "too many errors" means, and what to do about it, is `supervisor.py`'s job,
not the kernel's.

## Next steps

Continue to [Locking it down](locking-it-down.md), the series' final stage: it seals
capability access (`capability_access="sealed"`, `resolves=` grants), adds a typed
capability protocol, and introduces graceful shutdown — closing the honest gap
tutorial 5 left open around Ctrl-C.

- [Architecture overview](../explanation/architecture-overview.md) — the registry and
  capability system sections cover `PluginView`/`CapabilityInfo` in more depth
- [Use hot reload](../how-to/how-to-use-hot-reload.md) — the swap mechanics behind the
  `core.plugin_reloaded`/`capability.rebound` events `roster.py` listens for
