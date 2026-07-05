# Time and the tick

Starting from the `chat/` package [Deterministic conversations](deterministic-conversations.md)
left you with, you will add a folder watcher: a plugin that periodically checks this
folder's files for changes and hot-reloads whichever one changed — no restart, and no
new call to `load_plugin` written by you each time.

## What you will change

One new module, three small edits:

- `watcher.py` *(new)* — scans `watch_dir` on a self-rescheduling hook; hot-loads any
  file whose modification time changed
- `host.py` — gains a `watch_dir` entry in `host_configs()`, a subscription that surfaces
  a bad reload instead of swallowing it, and lingers after the scripted conversation
  instead of exiting immediately

Running it prints the same six lines tutorial 4 printed, then a new invitation to edit a
file live:

```text
user:  hello there
agent: Cheerfully #1: you said 'hello there'.
user:  what's the weather like?
agent: Cheerfully #2: you said 'what's the weather like?'.
persona: Cheerfully #3:
persona: Grumpily #4:
...[hot-reloaded persona]...
conversation done — edit a plugin file and save to see it hot-reload live
(Ctrl-C to exit)
```

## Why time needs a name

Everything so far in this series reacted to something: an event arriving, a script
calling a method. The watcher is the first thing that needs to act on its own, on a
schedule, with nothing to react to. It needs a notion of "check again in a bit" —
without reaching for wall-clock time or a background `asyncio.sleep` loop.

The kernel's answer is `core.tick`: a logical clock the core advances on its own, plus
`hook(name, at_tick=core.tick + n)` — arm a hook to fire `n` ticks from now. Calling it is
fire-and-forget: no `await`, no return value to wait on. It schedules the call and
returns immediately.

## `watcher.py`: scanning on a schedule

```python
"""Watcher — hot-reloads plugin files that change on disk.

Scans ``watch_dir`` on a self-rescheduling hook (``hook(name, at_tick=core.tick
+ n)``); a changed file hot-swaps the plugin of the same name via the
``kernel.lifecycle`` facet, a new file becomes a fresh plugin. ``watch_dir`` is
``REQUIRED``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from uxok import REQUIRED, ConfigField, CoreError, Plugin, PluginError

logger = logging.getLogger(__name__)

_NOT_PLUGINS = {"__init__.py", "host.py"}


class Watcher(Plugin):
    """Scans a folder every few ticks; hot-loads any plugin file that changed."""

    def __init__(self) -> None:
        super().__init__(
            name="watcher",
            requires={"kernel.lifecycle"},
            events_published={"watcher.reloaded", "watcher.load_failed"},
            config_schema={
                "watch_dir": ConfigField(str, REQUIRED, "folder whose *.py files are watched"),
                "interval_seconds": ConfigField(float, 0.25, "scan period, in seconds"),
            },
        )
        self._mtimes: dict[Path, float] = {}

    async def on_start(self) -> None:
        self._lifecycle = await self.get_capability("kernel.lifecycle")
        self._dir = Path(self.config("watch_dir"))
        self._mtimes = {path: path.stat().st_mtime for path in self._files()}
        self._interval = max(1, int(self.config("interval_seconds") * self.core.config.tick_rate))
        await self.register_hook("watcher.scan", self._scan)
        self.hook("watcher.scan", at_tick=self.core.tick + self._interval)

    def _files(self) -> list[Path]:
        return [p for p in sorted(self._dir.glob("*.py")) if p.name not in _NOT_PLUGINS]

    async def _scan(self) -> None:
        for path in self._files():
            mtime = path.stat().st_mtime
            if mtime == self._mtimes.get(path):
                continue
            self._mtimes[path] = mtime
            try:
                await self._lifecycle.load_plugin(path.read_text(), origin=str(path))
            except (PluginError, CoreError) as exc:
                logger.warning("watcher: load of %s failed: %s", path.name, exc)
                await self.emit("watcher.load_failed", {"origin": str(path), "error": str(exc)})
            else:
                await self.emit("watcher.reloaded", {"origin": str(path)})
        self.hook("watcher.scan", at_tick=self.core.tick + self._interval)
```

Two things carry the design:

- **Self-rescheduling**: `_scan()` calls `self.hook("watcher.scan", at_tick=...)` again as
  the last thing it does — arming its own next run. That's the entire mechanism for
  "repeat every N ticks." No timer object, no sleeping loop, nothing beyond `at_tick=`
  that you haven't already seen.
- **`register_hook` isn't new API, just dynamic wiring**: `@hook` (used in earlier
  tutorials) wires a fixed callback under a fixed name when the plugin starts.
  `register_hook(name, callback)` does the same registration, just at runtime — which is
  what lets `on_start` name `"watcher.scan"` once and then arm it by name via
  `self.hook(name, at_tick=...)`. `@hook` desugars to this; nothing new was added to the
  kernel for it.
- **`kernel.lifecycle` reused, not reintroduced**: `_scan()` hot-swaps a changed file with
  `load_plugin(code, origin=path)` — the exact call [Persona hot-reload](hot-reload.md)'s
  `conductor.py` already made once, scripted. What's new here is *what decides when to
  call it*: a file's modification time, not a line in a script. Same primitive, a
  different, self-driven trigger.

## `host.py`: watching this folder, and lingering to show it

```python
def host_configs() -> dict[str, dict[str, object]]:
    """Per-plugin configuration, validated against each plugin's ``config_schema``."""
    return {
        "agent": {"model_tag": "prose"},  # which llm provider the agent asks for
        "watcher": {"watch_dir": str(_HERE)},  # REQUIRED — no default exists
    }
```

`watch_dir` has no sane default — a watcher watching some directory nobody chose isn't
useful — so, like `model_tag` in [Configuration and tagged providers](configuration.md),
it's `REQUIRED` and supplied by the host.

The other change is in `main()`. Once the scripted conversation ends, tutorial 4's
`host.py` returned right away. That would never give a reader time to edit a file and see
the watcher react, so `main()` now prints an invitation and lingers instead:

```python
        await done.wait()  # stay alive until the conductor announces it is done
        print("conversation done — edit a plugin file and save to see it hot-reload live")
        print("(Ctrl-C to exit)")
        await asyncio.Event().wait()  # linger so the watcher can show off hot-reload
```

`main()` also subscribes to `"watcher.load_failed"` and prints it — a bad edit (say, a
syntax error) shouldn't be silently swallowed:

```python
        async def _load_failed(ev: Event) -> None:
            print(f"watcher: load_failed {ev.data['origin']}: {ev.data['error']}")

        await core.events.subscribe("watcher.load_failed", _load_failed)
```

There's no equivalent subscription for the success case. `conductor.py` (unchanged since
[Persona hot-reload](hot-reload.md)) already listens for `"core.plugin_reloaded"` and
prints `...[hot-reloaded <name>]...` for *any* reload — including one the watcher
triggers. Editing `persona.py` while the process lingers fires that same line again,
proving the swap happened, with nothing new to write.

## A gap, named honestly

Pressing Ctrl-C right now raises a bare `KeyboardInterrupt` — there's no graceful
shutdown yet. That's not an oversight: signal handling belongs to a later stage of this
series, once there's a dedicated plugin to own it. For now, Ctrl-C exits, just not
quietly. The tutorial says so plainly rather than pretending otherwise, the same way
[Deterministic conversations](deterministic-conversations.md) named `host.py`'s temporary
ownership of the driving loop instead of hiding it.

## Run it

```bash
python -m chat.host
```

The first six lines are unchanged from tutorial 4. Then, while the process is lingering,
open `persona.py` in another editor, change `"Cheerfully"` to anything else, and save.
Within about a second (the default `interval_seconds`) you'll see:

```text
...[hot-reloaded persona]...
```

Try breaking it, too: introduce a syntax error into a watched file while the host is
still running. Instead of a crash, you'll see:

```text
watcher: load_failed /path/to/persona.py: Failed to compile plugin code: ...
```

The watcher keeps scanning — one bad file doesn't stop it from noticing the next good
edit.

## The key idea

Time-based work doesn't need a new kernel object — a hook that reschedules itself *is*
"run every N ticks." And the same `kernel.lifecycle` facet that drove one scripted swap
now drives arbitrarily many, triggered by whatever policy a plugin chooses — a file
timestamp here, a script's next line in tutorial 2. The kernel provides the mechanism
once; what triggers it is downloaded policy, not kernel code.

## Next steps

Continue to [Watching the graph](watching-the-graph.md): add a roster that mirrors the
live registry and a supervisor that evicts repeat offenders — both observing what
plugins like the watcher do.

`example_host/`'s `roster.py` and `supervisor.py` are exactly that pairing. See
[`examples/example_host/`](https://github.com/hiddenfalls42/uxok/tree/main/examples/example_host)
for where this series ends up.

- [Use hot reload](../how-to/how-to-use-hot-reload.md) — the swap mechanics underneath
  `load_plugin` that this tutorial took for granted
- [Architecture overview](../explanation/architecture-overview.md) — "How hot reloading
  works," the isolated-module and rollback-on-failure details behind `load_plugin`
