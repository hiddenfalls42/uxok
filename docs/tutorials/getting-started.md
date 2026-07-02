# Getting started

Build your first uxok program — structured the way a real one is. You will write
two plugins, each in its own module, and a small *host* module that composes them
on a `Core` and runs them. By the end you will have a package you can run and
verify.

The program these plugins make — a two-line toy conversation — is beside the
point. The point is the **shape**: how you define a plugin, how plugins declare
their relationships and talk to each other, and how a host wires them together.
Learn that shape here and it carries over to whatever you actually build.

The [README](https://github.com/hiddenfalls42/uxok#quick-start) shows the same
program crammed into a single script — handy for a quick look. This tutorial
teaches the layout you actually want: plugins as separate modules that never
import each other, wired together only through the kernel.

## What you will build

A package called `chat/` with three modules:

- `model.py` — a plugin that *provides* an `"llm"` capability and contributes a
  `"persona"` hook
- `agent.py` — a plugin that *requires* `"llm"`, resolves it on start, and drives
  a short turn-by-turn conversation over the event bus
- `host.py` — creates a `Core`, registers the two plugins, and runs them to
  completion

Running it prints four lines — a user line and an agent line for each of two
turns. That is the test: if you see those lines, the capability system, the hook
system, and the event bus all worked.

**These are just plugins.** "Provider" and "consumer" name a *relationship*
around one capability, not two kinds of thing. `provides` and `requires` are
per-capability declarations: a single plugin can provide some capabilities and
require others at the same time. Here one plugin happens to provide `"llm"` and
the other happens to require it — which is which does not matter. Watch how they
are defined and wired, not what they compute.

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

A ready-to-run copy of this package lives in the repository at
[`examples/getting_started/`](https://github.com/hiddenfalls42/uxok/tree/main/examples/getting_started),
and is covered by `tests/test_getting_started.py` so it never drifts from what you
see here.

## The first plugin: `model.py`

A plugin is a class that subclasses `Plugin`. All constructor arguments — `name`,
`provides`, `requires`, and the rest — are keyword-only, and there is no `core`
parameter; the kernel attaches the core at registration time.

```python
"""Model — a plugin that provides the ``llm`` capability and the ``persona`` hook.

Stands in for an inference backend. It *provides* the ``llm`` capability; any
plugin that declares ``requires={"llm"}`` calls :meth:`reply` through the
capability surface without ever importing this class. The ``persona`` hook lets
any plugin ask "what voice should replies use?" without knowing who answers.
"""

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
`reply()` on it — without importing `Model`. The name `"llm"` is an arbitrary tag;
the two sides just have to agree on it.

`@hook("persona")` contributes to a named extension point. A hook is a question
any plugin can ask ("what voice should replies use?") and any plugin can answer;
`Model` answers with `"Cheerfully:"`. `reply` is written `async` so callers can
`await` it — a convention here, not a kernel rule.

## The second plugin: `agent.py`

```python
"""Agent — a plugin that requires the ``llm`` capability and drives the conversation.

Declares ``requires={"llm"}`` and resolves that capability by name in
``on_start`` — it never imports the sibling ``model`` module. It drives a short,
self-sustaining conversation over the event bus: each ``turn``
speaks one queued line, then re-emits ``turn`` for the next. When the queue is
empty it sets the ``done`` event, which lets the host shut down.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from uxok import Plugin, event

if TYPE_CHECKING:
    import asyncio

    from uxok.protocols import Event


class Agent(Plugin):
    """Requires ``llm``; speaks each queued line, then signals the host it is done."""

    def __init__(self, done: asyncio.Event) -> None:
        super().__init__(name="agent", requires={"llm"})
        self.lines = ["hello there", "what's the weather like?"]
        self.done = done

    async def on_start(self) -> None:
        # Resolved once by name; the capability surface hands back the live provider.
        self.llm = await self.get_capability("llm")
        await self.emit("turn")

    @event("turn")
    async def speak(self, _ev: Event) -> None:
        if not self.lines:
            self.done.set()  # conversation over — release the host
            return
        line = self.lines.pop(0)
        # The persona is resolved per reply through the hook, so a different
        # provider's voice is picked up immediately — no re-resolution here.
        persona = await self.hook("persona", firstresult=True)
        print(f"user:  {line}")  # noqa: T201 — demo output is the point
        print(f"agent: {await self.llm.reply(line, persona)}")  # noqa: T201
        await self.emit("turn")  # re-arm the loop for the next line
```

Three primitives appear here.

**Capabilities.** `on_start()` runs once when the plugin starts. It calls
`self.get_capability("llm")`, which returns whatever plugin provides `"llm"` —
here the `Model` instance — so `self.llm.reply(...)` calls it directly. This is
the canonical way a plugin reaches a dependency, the sibling of `self.emit` and
`self.hook`. (`self.core` also exposes `get_capability`, but the plugin method is
the one to reach for.) Resolution is *by name*; the plugin never imports the
`model` module and does not care which plugin answers.

**Events.** `self.emit("turn")` publishes an event; `@event("turn")` subscribes a
method to it. The agent drives itself: `on_start` emits the first `"turn"`, and
each `speak` emits the next after printing, until `self.lines` is empty — then it
sets the `done` event so the host can stop. The handler receives the `Event`, but
this one has no use for it, so it is named `_ev`. Event names are matched as glob
patterns, so `@event("turn.*")` would also match `turn.user.done`.

**Hooks.** `self.hook("persona", firstresult=True)` runs the `"persona"` hook and
takes the first answer. Drop `firstresult` and you get the full list of every
handler's result in priority order — a pipeline rather than a single answer.

## The host: `host.py`

```python
"""host.py — composes the getting-started conversation into a runnable program.

A *host* boots a :class:`~uxok.Core`, registers a graph of plugins on it in
dependency order, and keeps it alive until the work is done. This is the minimal,
modular sibling of the README quick-start: the same Model / Agent / persona-hook
conversation, but each plugin lives in its own module.

``build_host`` is shared by ``main`` and the test suite, so the running program
and the tested program never drift.

Run it:

    python -m examples.getting_started.host
"""

from __future__ import annotations

import asyncio

from uxok import Core

from .agent import Agent
from .model import Model


async def build_host(core: Core, done: asyncio.Event) -> None:
    """Register the two-plugin graph on ``core`` in dependency order.

    Whatever provides a capability must be registered before whatever requires
    it, so ``Model`` (provides ``llm``) comes up before ``Agent`` (requires it).
    """
    await core.register_plugin(Model())  # provides "llm"
    await core.register_plugin(Agent(done))  # requires "llm"


async def main() -> None:
    done = asyncio.Event()
    async with Core() as core:  # context manager starts/stops the kernel
        await build_host(core, done)
        await done.wait()  # stay alive until the agent finishes the conversation


if __name__ == "__main__":
    asyncio.run(main())
```

`Core` is the host: it owns the event bus, hook system, plugin registry, and
capability system. Used as an async context manager, `async with Core() as core`
starts the kernel on entry and tears it down on exit — no explicit
`start()`/`stop()` calls. (You can call `await core.start()` / `await core.stop()`
by hand when you need finer control — see
[Manage core lifecycle](../how-to/how-to-manage-core-lifecycle.md).)

**Registration order matters.** The kernel checks `requires` when
`register_plugin` is called and raises `MissingCapabilityError` immediately if no
registered plugin provides the capability. Register whatever provides a capability
before whatever requires it — which is why `build_host` adds `Model` before
`Agent`. That is the *only* ordering constraint; it follows from the `requires`
edges, not from any fixed idea of which plugin comes first.

The `asyncio.Event` is how `main()` knows when to stop. Registration returns
immediately, so without `await done.wait()` the block would exit before the
conversation ran. The agent calls `done.set()` when it runs out of lines, which
releases `wait()` and lets the context manager shut everything down.

Keeping `build_host` separate from `main` is a small but useful habit: the test
suite calls the same `build_host`, so the program you run and the program you test
can never drift apart.

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

Open `model.py` and `agent.py` side by side: they share no import. Neither plugin
names the other; one asks for the `"llm"` capability by name and the kernel hands
back whatever plugin provides it. The host is the only place the two meet, and it
wires them by capability, not by import.

That decoupling is the whole point of the structure, and it is what makes the
roles arbitrary. Swap in a different plugin that provides `"llm"`, or hot-reload
one at runtime, and the plugin that requires it never changes. Add a third plugin
that both requires `"llm"` and provides something new, and nothing already written
has to know. You are not building a fixed provider-and-consumer pair — you are
building plugins that declare what they need and offer, and letting the kernel
connect them.

## Next steps

For the same conversation with the features a real host leans on — a persona as
its own hot-reloadable plugin, live plugin swapping, and graceful signal-driven
shutdown — read
[`examples/example_host/`](https://github.com/hiddenfalls42/uxok/tree/main/examples/example_host),
the fuller sibling of this starter.

Each kernel primitive also has its own how-to and explanation pages.

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
