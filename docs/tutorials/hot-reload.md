# Persona hot-reload

Starting from the `chat/` package [Getting started](getting-started.md) built, you will
extract the inline `persona` hook into its own plugin, then swap it for a different
plugin *while the program is running* — no restart, and no lost state.

## What you will change

Two new modules, one small change to an existing one:

- `persona.py` *(new)* — the `persona` hook, extracted out of `model.py`, now counting
  how many replies it has voiced
- `grumpy_persona.py` *(new)* — a replacement `Persona` that never boots on its own; it
  only exists to be hot-loaded
- `conductor.py` *(new)* — proves the swap: asks the `persona` hook, hot-loads
  `grumpy_persona.py`, asks again
- `model.py` — loses the `@hook("persona")` method; it goes back to being purely the
  `llm` provider
- `agent.py` — one-line change to its final `emit`, explained below

Running it prints six lines instead of four:

```text
user:  hello there
agent: Cheerfully #1: you said 'hello there'.
user:  what's the weather like?
agent: Cheerfully #2: you said 'what's the weather like?'.
persona: Cheerfully #3:
persona: Grumpily #4:
...[hot-reloaded persona]...
```

The reply count keeps climbing (`#3`, `#4`) across the swap — that continuity is the
proof the hot-reload actually replaced the running instance rather than starting a
fresh one. (The last line's position isn't guaranteed: `core.plugin_reloaded` is
delivered by an independent, concurrently-dispatched handler, not synchronized with
`conductor.py`'s own two prints — more on that below.)

## Extracting the hook: `persona.py`

`model.py` loses its `@hook("persona")` method and the now-unused `hook` import,
leaving it as purely the `llm` provider. The hook moves to a plugin of its own:

```python
"""Persona — contributes the agent's voice through the ``persona`` hook.

The hot-reload target: ``get_state``/``restore_state`` are the state-handoff
contract the kernel calls across a swap, so this plugin's reply count
survives being replaced by ``grumpy_persona.py``.
"""

from __future__ import annotations

from uxok import Plugin, hook


class Persona(Plugin):
    """Answers the ``persona`` hook with a counted prefix; count survives hot-swap."""

    def __init__(self) -> None:
        super().__init__(name="persona")
        self._count = 0

    @hook("persona")
    async def voice(self) -> str:
        self._count += 1
        return f"Cheerfully #{self._count}:"

    async def get_state(self) -> dict:
        return {"count": self._count}

    async def restore_state(self, state: dict) -> None:
        self._count = state.get("count", 0)
```

Nothing about `agent.py`'s `self.hook("persona", firstresult=True)` call changes —
it never knew or cared which plugin answered the hook, so moving the answer to a new
plugin is invisible from the agent's side. That's the same by-name decoupling as
`provides`/`requires`, applied to hooks.

`get_state`/`restore_state` are new: they're the state-handoff contract the kernel
calls on a hot-swap — `get_state()` on the outgoing instance, `restore_state(state)`
on the incoming one — so `_count` survives being replaced entirely.

## The swap payload: `grumpy_persona.py`

```python
"""A replacement Persona, hot-loaded from source at runtime.

Never booted — ``build_host`` excludes it; ``conductor.py`` hands its source to
``core.load_plugin``. It resolves to the same plugin name (``persona``) as
``persona.py``, so the kernel swaps it in and carries the reply count across
via ``get_state``/``restore_state``.
"""

from uxok import Plugin, hook


class Persona(Plugin):
    def __init__(self) -> None:
        super().__init__(name="persona")
        self._count = 0

    @hook("persona")
    async def voice(self) -> str:
        self._count += 1
        return f"Grumpily #{self._count}:"

    async def get_state(self) -> dict:
        return {"count": self._count}

    async def restore_state(self, state: dict) -> None:
        self._count = state.get("count", 0)
```

This file is never part of the initial boot batch — `host.py` excludes it explicitly
(see below). It resolves to the *same plugin name*, `"persona"`, as `persona.py`. That
name match is what makes `core.load_plugin` treat this as a swap of the running
`persona` plugin rather than a second, conflicting one.

## Triggering the swap: `conductor.py`

Something has to actually call `core.load_plugin` with `grumpy_persona.py`'s source.
It would be tempting to have `host.py` do it directly — the host already has a `core`
reference in scope — but that couples the host to a specific plugin's filename and
behavior, which is exactly what the host's whole design (loading plugins by scanning
a folder, never naming one) exists to avoid. So a plugin does it instead:

```python
"""Conductor — proves the persona survives a live hot-swap.

Waits for the scripted conversation to finish, asks the ``persona`` hook
directly, hot-swaps in ``grumpy_persona.py`` through the ``kernel.lifecycle``
facet, and asks again — same reply count, new voice. Only then signals real
completion, so the swap finishes before the host tears the core down.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from uxok import Plugin, event

if TYPE_CHECKING:
    from uxok.protocols import Event

_GRUMPY = Path(__file__).resolve().parent / "grumpy_persona.py"


class Conductor(Plugin):
    """Hot-swaps the persona once the scripted conversation is done."""

    def __init__(self) -> None:
        super().__init__(name="conductor", requires={"kernel.lifecycle"})

    async def on_start(self) -> None:
        self._lifecycle = await self.get_capability("kernel.lifecycle")

    @event("agent.done")
    async def _swap(self, _ev: Event) -> None:
        print(f"persona: {await self.hook('persona', firstresult=True)}")  # noqa: T201
        await self._lifecycle.load_plugin(_GRUMPY.read_text(), origin=str(_GRUMPY))
        print(f"persona: {await self.hook('persona', firstresult=True)}")  # noqa: T201
        await self.emit("conversation.over")

    @event("core.plugin_reloaded")
    async def _on_reloaded(self, ev: Event) -> None:
        print(f"...[hot-reloaded {ev.data['plugin_name']}]...")  # noqa: T201
```

`requires={"kernel.lifecycle"}` is a reserved grant — always satisfiable, no provider
needed — that resolves to a facet exposing graph-control methods including
`load_plugin`. Any plugin can trigger a hot-swap this way; the host never needs to.

`core.plugin_reloaded` is a framework event the kernel publishes whenever a hot-swap
lands. Subscribing to it is how a plugin *observes* a swap happening (its own or
another plugin's) rather than assuming one just because it triggered it.

## The one change to `agent.py`

Stage 1's `agent.py` emitted `"conversation.over"` directly when its line queue
emptied, and `host.py`'s `main()` waits on exactly that event to shut the program
down. If `conductor.py` also reacted to `"conversation.over"` to run its swap
demonstration, the two subscribers would race: event dispatch is concurrent, so the
host could tear the core down before the conductor's hook calls and `load_plugin`
finish.

So the signal splits in two. `agent.py` now emits `"agent.done"` when it's out of
lines — a private "the scripted part is finished" signal:

```python
    @event("turn")
    async def speak(self, _ev: Event) -> None:
        if not self.lines:
            await self.emit("agent.done")  # let the conductor run before the host stops
            return
```

`conductor.py` reacts to `"agent.done"`, runs its demonstration to completion, and
only then emits `"conversation.over"` — the event `host.py` still waits on, unchanged.
Agent finishes → conductor proves the swap → conductor signals real completion → host
stops. This is the smallest fix that doesn't require shutdown infrastructure (that
arrives in a later stage) or a sleep (which would just hide the race, not fix it).

## The one change to `host.py`

`build_host`'s folder scan needs to skip `grumpy_persona.py`, or the initial batch
would try to load two plugins both named `"persona"`:

```python
_HOST_FILES = {"__init__.py", "host.py"}
_SWAP_PAYLOADS = {"grumpy_persona.py"}  # hot-reloaded in later; not part of the boot graph


async def build_host(core: Core) -> None:
    """Load every plugin module in this folder, regardless of dependency order."""
    skip = _HOST_FILES | _SWAP_PAYLOADS
    paths = [path for path in sorted(_HERE.glob("*.py")) if path.name not in skip]
    await core.load_plugins([(path.read_text(), str(path)) for path in paths])
```

`main()`'s subscription to `"conversation.over"` doesn't change at all — it just fires
from `conductor.py` now instead of `agent.py`. The host still doesn't name a single
plugin anywhere in its own logic.

## Run it

```bash
python -m chat.host
```

Expected output:

```text
user:  hello there
agent: Cheerfully #1: you said 'hello there'.
user:  what's the weather like?
agent: Cheerfully #2: you said 'what's the weather like?'.
persona: Cheerfully #3:
persona: Grumpily #4:
...[hot-reloaded persona]...
```

## The key idea

A hot-swap is just `core.load_plugin` handed source whose class resolves to a name
already running. Same name, same slot: the kernel treats it as a replacement, not a
new registration, and hands the outgoing instance's `get_state()` to the incoming
instance's `restore_state()` so it can pick up where the old one left off.

Nothing that *asks* the `persona` hook — `agent.py` — had to change at all. Only the
thing that *triggers* the swap needed writing, and it's a plugin (`conductor.py`), not
the host. That's the same rule as `provides`/`requires`, extended one level further:
not just "who answers a capability" but "who's allowed to change the graph while it's
running" stays out of the host's hands.

## Next steps

Continue to [Configuration and tagged providers](configuration.md): add a second,
competing `"llm"` provider and let config decide which one the agent talks to.

For the same conversation with the rest of the features a real host leans on — a
watcher that hot-loads edited plugin files, a supervisor consuming the kernel's error
signals, and signal-driven shutdown — read
[`examples/example_host/`](https://github.com/hiddenfalls42/uxok/tree/main/examples/example_host),
the destination this series grows into. `conductor.py` there is this same plugin,
grown twice more: correlated request/reply drives the conversation itself, and it
queries a roster plugin for a live graph report.

- [Use hot reload](../how-to/how-to-use-hot-reload.md) — `core.load_plugin()`, state
  handoff, in more depth
- [Capability system](../explanation/capability-system.md) — the `kernel.lifecycle`
  reserved grant and other always-satisfiable capabilities
- [Event system](../explanation/event-system.md) — why dispatch is concurrent, and
  what that does and doesn't guarantee about ordering
