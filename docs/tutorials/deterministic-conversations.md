# Deterministic conversations

Starting from the `chat/` package [Configuration and tagged providers](configuration.md)
left you with, you will replace `agent.py`'s fixed turn loop with correlated
request/reply — each question gets a matching answer, not just "whatever comes next."

## What you will change

Two edits, no new modules:

- `agent.py` — loses `self.lines` and its own printing; becomes a pure responder that
  answers `"user.says"` requests on a background task
- `host.py` — gains `run_script()`, which drives the two demo lines through correlated
  request/reply and takes over printing the exchange

Running it prints exactly what tutorial 3 printed — this stage changes *how* the
exchange is driven, not what you see:

```text
user:  hello there
agent: Cheerfully #1: you said 'hello there'.
user:  what's the weather like?
agent: Cheerfully #2: you said 'what's the weather like?'.
persona: Cheerfully #3:
persona: Grumpily #4:
...[hot-reloaded persona]...
```

## Why the turn loop had to go

Tutorial 3's `agent.py` decided for itself when to ask the next question:

```python
    @event("turn")
    async def speak(self, _ev: Event) -> None:
        ...
        await self.emit("turn")  # re-arm the loop for the next line
```

That only works because the script is scripted — the agent already knows there's
exactly one more line to say next. A real agent doesn't control when its next input
arrives, and when it does arrive, the agent needs to answer *that* request, not
whichever one happens to be up next in some internal list. Correlated request/reply is
the fix: each question carries a correlation id (`cid`), and each answer targets that
exact `cid`.

## `agent.py`: answering on demand

```python
"""Agent — answers ``user.says`` requests with a correlated ``agent.says.<cid>`` reply.

Deterministic request/reply, not tutorial 3's fixed turn loop: whoever asks a question
now waits for its actual answer, correlated by ``cid`` — not by dispatch order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from uxok import REQUIRED, ConfigField, Plugin, event

if TYPE_CHECKING:
    from uxok.protocols import Event


class Agent(Plugin):
    """Requires ``llm``; answers on demand instead of driving its own turn loop."""

    def __init__(self) -> None:
        super().__init__(
            name="agent",
            requires={"llm"},
            config_schema={
                "model_tag": ConfigField(str, REQUIRED, "tag of the llm provider to talk to"),
            },
        )

    async def on_start(self) -> None:
        self.llm = await self.get_capability("llm", tag=self.config("model_tag"))

    @event("user.says")
    async def respond(self, ev: Event) -> None:
        await self.create_background_task(self._answer(ev.data["cid"], ev.data["text"]))

    async def _answer(self, cid: str, text: str) -> None:
        reply_topic = f"agent.says.{cid}"
        if not self.has_subscribers(reply_topic):
            return
        persona = await self.hook("persona", firstresult=True)
        reply = await self.llm.reply(text, persona)
        await self.emit(reply_topic, {"text": reply})
```

Two new `Plugin` methods carry the design:

- **`create_background_task`** means `respond()` returns immediately once it has handed
  off the work. `_answer()` runs independently, so a slow model reply never blocks the
  next incoming request from being accepted.
- **`has_subscribers`** is a demand gate: skip the persona lookup and the model call
  entirely if nobody's listening for this `cid`'s reply. In *this* script every `cid` is
  genuinely awaited, so the gate is always true here — it earns its keep once a
  requester can time out and stop listening before the responder finishes, which this
  script doesn't exercise.

## The cid-in-topic grammar

Notice the asymmetry: the *request* (`"user.says"`) is one fixed topic, with `cid`
riding along in the event data. The *reply* embeds `cid` in the topic name itself
(`agent.says.<cid>`). That's not an arbitrary choice — the event bus has no return
value, so a topic-per-request is what lets an otherwise-anonymous fire-and-forget
publish get routed back to exactly one waiter. The requester always knows which `cid`
it's waiting on, so the request side doesn't need it in the topic name — only the reply
side does, because the reply is what needs addressing back to a specific asker.

## `host.py`: driving the script

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

from uxok import Core
from uxok.protocols import Event

_HERE = Path(__file__).resolve().parent
_HOST_FILES = {"__init__.py", "host.py"}
_SWAP_PAYLOADS = {"grumpy_persona.py"}  # hot-reloaded in later; not part of the boot graph

SCRIPT = ["hello there", "what's the weather like?"]


def host_configs() -> dict[str, dict[str, object]]:
    """Per-plugin configuration, validated against each plugin's ``config_schema``."""
    return {
        "agent": {"model_tag": "prose"},  # which llm provider the agent asks for
    }


async def build_host(core: Core) -> None:
    """Load every plugin module in this folder, regardless of dependency order."""
    skip = _HOST_FILES | _SWAP_PAYLOADS
    paths = [path for path in sorted(_HERE.glob("*.py")) if path.name not in skip]
    await core.load_plugins([(path.read_text(), str(path)) for path in paths])


async def run_script(core: Core) -> None:
    """Ask each line in SCRIPT, waiting for its real reply before asking the next."""
    pending: dict[str, asyncio.Future[str]] = {}

    async def _on_reply(ev: Event) -> None:
        cid = ev.name.rsplit(".", 1)[-1]
        future = pending.pop(cid, None)
        if future is not None and not future.done():
            future.set_result(ev.data["text"])

    await core.events.subscribe("agent.says.*", _on_reply)
    for i, line in enumerate(SCRIPT, start=1):
        cid = f"c{i}"
        pending[cid] = asyncio.get_running_loop().create_future()
        print(f"user:  {line}")  # noqa: T201
        await core.events.publish(Event("user.says", {"cid": cid, "text": line}))
        print(f"agent: {await asyncio.wait_for(pending[cid], timeout=2.0)}")  # noqa: T201
    await core.events.publish(Event("agent.done", {}))


async def main() -> None:
    done = asyncio.Event()
    async with Core(plugin_configs=host_configs()) as core:  # starts/stops the kernel

        async def _stop(_ev: Event) -> None:
            done.set()

        await core.events.subscribe("conversation.over", _stop)
        await build_host(core)
        await run_script(core)
        await done.wait()  # stay alive until the conductor announces it is done


if __name__ == "__main__":
    asyncio.run(main())
```

`run_script` subscribes to `"agent.says.*"` **once**, not once per question —
`core.events.subscribe` (the same mechanism the `@event` decorator desugars to) already
supports `fnmatch`-style glob patterns; nothing new was added to the kernel to make this
work, it's an existing capability applied to a request/reply grammar built on top of it.
Each turn creates a `Future`, publishes the request, and awaits that future with a
timeout — deterministic, not "wait a fixed delay and hope."

`Agent` no longer emits `"agent.done"` itself — it doesn't drive a script anymore, so it
has nothing to signal the end of. `host.py`'s `run_script` emits it once the last line
has its reply, which is enough to keep `conductor.py`'s existing
`@event("agent.done")` trigger firing exactly as before. `conductor.py` itself is
untouched.

One more field worth knowing: `Event.source`. Events built directly via
`core.events.publish(Event(...))` — as `run_script` does here — carry `source=None`.
Events sent through `self.emit()` — as `Agent.respond`'s reply does — get `source`
stamped automatically with the emitting plugin's name. Nothing in this script reads
`source`, but it's the field to reach for when you're debugging who actually published
something.

## A tension, named honestly

`host.py` now contains `SCRIPT` and the loop that drives it — content a "the host never
mentions plugin behavior" design would rather keep out of the host. This is temporary
scaffolding, not a reversal of that principle: RFC 0009's staging put the request/reply
driving logic here because, at this point in the series, there's no dedicated plugin
free to own it yet (`conductor.py` is already the persona-swap demo). Once the series
reaches hot-reload-driven orchestration, this driving loop moves into a plugin of its
own — the real `conductor.py` in
[`examples/example_host/`](https://github.com/hiddenfalls42/uxok/tree/main/examples/example_host)
does exactly that, keyed by the same `cid`/future pattern shown here.

## Run it

```bash
python -m chat.host
```

Expected output — identical to tutorial 3's, because this stage changes the mechanism,
not the transcript:

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

An event bus with no return value gets replies routed back by giving each request a
`cid` and giving the reply its own topic. "Who answers" stays exactly as decoupled as
before — still just `requires={"llm"}`, resolved by tag — but "which specific exchange
this answer belongs to" is now explicit instead of implied by turn order.

## Next steps

Continue to [Time and the tick](time-and-the-tick.md): add a folder watcher that
hot-reloads changed plugin files on its own schedule, using the same `kernel.lifecycle`
facet this stage relied on.

`example_host/`'s `conductor.py` takes this further still: the driving logic shown here in
`host.py` moves into a dedicated plugin once the series reaches hot-reload-driven
orchestration — see
[`examples/example_host/`](https://github.com/hiddenfalls42/uxok/tree/main/examples/example_host)
for where this series ends up.

- [Event system](../explanation/event-system.md) — glob subscriptions, demand-gated
  emission, and the rest of the event bus
- [Use hot reload](../how-to/how-to-use-hot-reload.md) — background tasks and state
  handoff in more depth
