# Getting started

You will build a small package called `chat/`: two plugins and a *host* that loads
whatever plugin modules are in the folder. The program is a toy — a two-line
conversation — but the shape is real: plugins that never import each other,
connected only through the kernel by name. The
[README](https://github.com/hiddenfalls42/uxok#quick-start) shows the same program
crammed into one script — wired differently, too: there the script constructs the
plugin instances and hands them to `register_plugin`, where this host loads their
source. This tutorial teaches the layout you actually want.

## What you will build

A package called `chat/` with three modules:

- `model.py` — a plugin that *provides* an `"llm"` capability and answers a
  `"persona"` hook
- `agent.py` — a plugin that *requires* `"llm"`, drives a turn-by-turn conversation
  over the event bus, and emits a done event when finished
- `host.py` — a tiny host that loads plugin modules from *source* and stays alive
  until the plugins finish

Running it prints four lines:

```text
user:  hello there
agent: Cheerfully: you said 'hello there'.
user:  what's the weather like?
agent: Cheerfully: you said 'what's the weather like?'.
```

If you see those lines, the capability system, the hook system, and the event bus
all worked.

**These are just plugins.** "Provider" and "consumer" name a *relationship* around
one capability, not two kinds of thing: `provides` and `requires` are per-capability
declarations, and a single plugin can do both at once. Here one plugin happens to
provide `"llm"` and the other to require it. Watch how they are wired, not what
they compute.

## Prerequisites

- Python 3.12 or higher
- Familiarity with `async`/`await` at the level of reading the standard library docs

## Install

```bash
pip install uxok
```

## Project layout

Create a package — a folder with an `__init__.py` — holding the three modules:

```text
chat/
├── __init__.py   # empty is fine
├── model.py
├── agent.py
└── host.py
```

A ready-to-run copy lives in the repository at
[`examples/getting_started/`](https://github.com/hiddenfalls42/uxok/tree/main/examples/getting_started),
kept in sync with what you see here by `tests/test_getting_started.py`.

## The first plugin: `model.py`

A plugin is a class that subclasses `Plugin`. Constructor arguments are keyword-only,
and there is no `core` parameter — the kernel attaches the core at registration time.

```python
"""Model — a plugin that provides the ``llm`` capability and the ``persona`` hook."""

from __future__ import annotations

from uxok import Plugin, hook


class Model(Plugin):
    """Provides ``llm``: turns a prompt (plus a persona prefix) into a reply."""

    def __init__(self) -> None:
        super().__init__(name="model", provides={"llm"})

    async def reply(self, text: str, persona: str) -> str:
        return f"{persona} you said '{text}'."

    @hook("persona")
    async def voice(self) -> str:
        return "Cheerfully:"
```

`provides={"llm"}` declares that this plugin provides the `"llm"` capability. Any
plugin that declares `requires={"llm"}` can then fetch this instance and call
`reply()` on it without importing `Model`; `"llm"` is an arbitrary tag the two sides
just have to agree on. `@hook("persona")` contributes an answer to a named extension
point — a question any plugin can ask ("what voice should replies use?") and any
plugin can answer.

## The second plugin: `agent.py`

```python
"""Agent — a plugin that requires ``llm`` and drives an event-driven conversation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from uxok import Plugin, event

if TYPE_CHECKING:
    from uxok.protocols import Event


class Agent(Plugin):
    """Requires ``llm``; speaks each queued line, then announces it is done."""

    def __init__(self) -> None:
        super().__init__(name="agent", requires={"llm"})
        self.lines = ["hello there", "what's the weather like?"]

    async def on_start(self) -> None:
        # Resolved once by name; the capability surface hands back the live provider.
        self.llm = await self.get_capability("llm")
        await self.emit("turn")

    @event("turn")
    async def speak(self, _ev: Event) -> None:
        if not self.lines:
            await self.emit("conversation.over")  # let the host shut down
            return
        line = self.lines.pop(0)
        # The persona is resolved per reply through the hook, so a different
        # provider's voice is picked up immediately — no re-resolution here.
        persona = await self.hook("persona", firstresult=True)
        print(f"user:  {line}")  # noqa: T201 — demo output is the point
        print(f"agent: {await self.llm.reply(line, persona)}")  # noqa: T201
        await self.emit("turn")  # re-arm the loop for the next line
```

**Capabilities.** `on_start()` runs once when the plugin starts. It calls
`self.get_capability("llm")`, which returns whatever plugin provides `"llm"` — here
the `Model` instance — so `self.llm.reply(...)` calls it directly. Resolution is *by
name*: the agent never imports the `model` module and does not care which plugin
answers.

**Events.** `self.emit("turn")` publishes an event; `@event("turn")` subscribes a
method to it. The agent drives itself — `on_start` emits the first `"turn"`, each
`speak` emits the next, and when the queue empties it emits `"conversation.over"`
instead, the event the host waits on. Event names are matched as glob patterns, so
`@event("turn.*")` would also match `turn.user.done`.

**Hooks.** `self.hook("persona", firstresult=True)` runs the `"persona"` hook and
takes the first answer. Drop `firstresult` and you get every handler's result in
priority order — a pipeline rather than a single answer.

## The host: `host.py`

```python
"""host.py — a tiny hot-loader: it loads plugin files from this folder.

Rather than importing plugin classes or naming them in dependency order, it hands
every plugin module's source to ``core.load_plugins`` and lets the kernel work out
the load order from each plugin's declared capabilities. Run it as a module:
``python -m <package>.host``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from uxok import Core

if TYPE_CHECKING:
    from uxok.protocols import Event

_HERE = Path(__file__).resolve().parent
_HOST_FILES = {"__init__.py", "host.py"}


async def build_host(core: Core) -> None:
    """Load every plugin module in this folder, regardless of dependency order."""
    paths = [path for path in sorted(_HERE.glob("*.py")) if path.name not in _HOST_FILES]
    await core.load_plugins([(path.read_text(), str(path)) for path in paths])


async def main() -> None:
    done = asyncio.Event()
    async with Core() as core:  # context manager starts/stops the kernel

        async def _stop(_ev: Event) -> None:
            done.set()

        await core.events.subscribe("conversation.over", _stop)
        await build_host(core)
        await done.wait()  # stay alive until the agent announces it is done


if __name__ == "__main__":
    asyncio.run(main())
```

`Core` is the host: it owns the event bus, hook system, plugin registry, and
capability system. Used as an async context manager, `async with Core() as core`
starts the kernel on entry and tears it down on exit. (For manual `start()`/`stop()`,
see [manage core lifecycle](../how-to/how-to-manage-core-lifecycle.md).)

**It loads plugins from source, it does not import them.** `build_host` never
mentions `model.py`, `agent.py`, `Model`, or `Agent`. It scans the folder, reads each
candidate plugin file, hands the whole set to `core.load_plugins(...)`, and the kernel
compiles, registers, and starts each one. Because the kernel builds the instance
itself, hot-loaded plugins take **no constructor arguments**. That is why the agent
carries its own line queue and signals completion by event. This is uxok's
"downloaded policy"; see the
[capability system](../explanation/capability-system.md) and
[use hot reload](../how-to/how-to-use-hot-reload.md) for why it matters.

**Load order still follows the `requires` edges, but the host does not name them.**
`load_plugins` materializes every source, reads each plugin's `provides`/`requires`,
and computes a topological order so every provider starts before the plugins that
require it — then commits the whole batch under a single hold of the lifecycle lock.
A malformed graph fails as a unit: a missing capability or a dependency cycle raises
`BatchLoadError` in the *plan* phase, before any plugin is registered, so a bad graph
never leaves half a system running. When you would rather boot whatever resolves and
collect the rest — a folder of independently authored plugins where one bad file must
not empty the boot — reach for the best-effort sibling `core.try_load_plugins()`, which
returns a skip report instead of raising. See
[boot a plugin graph in order](../how-to/how-to-boot-a-plugin-graph-in-order.md)
for both verbs, the rollback recipes, and the lower-level manual alternative.

`main()` subscribes `_stop` to `"conversation.over"` *before* loading the plugins,
then waits on an `asyncio.Event` the handler sets. Loading the agent starts it, so
the conversation begins at once. Registration returns immediately, so without
`await done.wait()` the block would exit before anything ran.

## Run it

From the folder *containing* `chat/`:

```bash
python -m chat.host
```

Expected output:

```text
user:  hello there
agent: Cheerfully: you said 'hello there'.
user:  what's the weather like?
agent: Cheerfully: you said 'what's the weather like?'.
```

## The key idea

Notice what imports what: nothing. `model.py` and `agent.py` share no import — one
asks for `"llm"` by name and the kernel hands back whatever provides it. `host.py`
imports *neither* plugin class — it loads them as source and coordinates through one
named event. The only things that cross a module boundary are strings: a capability
name, an event name, never a class.

That is what makes the roles arbitrary. Swap in a different plugin that provides
`"llm"`, or hot-reload one at runtime, and the plugin that requires it never changes.
You are not wiring a fixed provider-and-consumer pair together at import time — you
are handing the kernel plugins that declare what they need and offer, and letting it
connect them at runtime.

## Next steps

For the same conversation with the features a real host leans on — a persona whose
state survives live hot-swaps, competing model providers selected by config, a
watcher that hot-loads edited plugin files, a supervisor consuming the kernel's
error signals, and signal-driven shutdown — read
[`examples/example_host/`](https://github.com/hiddenfalls42/uxok/tree/main/examples/example_host),
the destination this starter grows into.

To go deeper on any one primitive:

- [Capability system](../explanation/capability-system.md) — resolution by name, provider selection
- [Event system](../explanation/event-system.md) — pub/sub design, concurrent dispatch
- [Use hot reload](../how-to/how-to-use-hot-reload.md) — `core.load_plugin()`, state handoff
- [Use tags for provider selection](../how-to/how-to-use-tags-for-provider-selection.md) —
  the first thing you need once two plugins provide the same capability

The [how-to](../how-to/index.md) and [explanation](../explanation/index.md) index
pages list the rest, one page per primitive.
