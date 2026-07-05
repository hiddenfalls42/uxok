# Configuration and tagged providers

Starting from the `chat/` package [Persona hot-reload](hot-reload.md) left you with, you
will make the `"llm"` capability *contested* — a second plugin provides it too — and
give the host a place to configure which one the agent talks to.

## What you will change

One new module, three small edits:

- `terse_model.py` *(new)* — a second `"llm"` provider, tagged `"terse"`
- `model.py` — tagged `"prose"`, and its hardcoded punctuation becomes a config field
- `agent.py` — picks its provider by tag, read from its own config
- `host.py` — gains `host_configs()`, handed to `Core(plugin_configs=...)`

Running it with the default config prints exactly what tutorial 2 printed — this stage
is about what's now *configurable*, not a new transcript:

```text
user:  hello there
agent: Cheerfully #1: you said 'hello there'.
user:  what's the weather like?
agent: Cheerfully #2: you said 'what's the weather like?'.
persona: Cheerfully #3:
persona: Grumpily #4:
...[hot-reloaded persona]...
```

## The second provider: `terse_model.py`

```python
"""TerseModel — a second, competing provider of the ``llm`` capability.

Alongside ``model.py`` this makes ``llm`` a *contested* capability: both
providers live under the default ``last_wins_with_warning`` collision policy,
and the agent picks one with ``get_capability("llm", tag=...)``.
"""

from __future__ import annotations

from uxok import Plugin


class TerseModel(Plugin):
    """Provides ``llm``, tagged ``terse``: answers in as few words as possible."""

    def __init__(self) -> None:
        super().__init__(name="terse_model", provides={"llm"}, tags={"terse"})

    async def reply(self, text: str, persona: str) -> str:
        return f"{persona} {text}? noted."
```

`tags=` is a set carried on the plugin's metadata — an axis orthogonal to the capability
name. Two plugins can both `provide={"llm"}`; a `tag` is how a consumer narrows *which*
one it means without inventing a second capability name for the same shape.

## `model.py`: tagged, and configurable

```python
"""Model — provides the ``llm`` capability, tagged ``prose``.

``tags={"prose"}`` distinguishes this provider from ``terse_model.py``, the
other ``llm`` provider — same capability name, two candidates, picked by tag.
"""

from __future__ import annotations

from uxok import ConfigField, Plugin


class Model(Plugin):
    """Provides ``llm``, tagged ``prose``: turns a prompt into a full sentence."""

    def __init__(self) -> None:
        super().__init__(
            name="model",
            provides={"llm"},
            tags={"prose"},
            config_schema={
                "suffix": ConfigField(str, ".", "sentence-final punctuation"),
            },
        )

    async def reply(self, text: str, persona: str) -> str:
        return f"{persona} you said '{text}'{self.config('suffix')}"
```

`config_schema` declares what a plugin accepts; `ConfigField(type, default, description)`
is one entry. `suffix` has a sane default — every deployment can leave it alone — so
`ConfigField(str, ".", ...)` is enough. `self.config("suffix")` reads back whatever value
ended up in effect, default or supplied.

## `agent.py`: picking a provider by tag

```python
from uxok import REQUIRED, ConfigField, Plugin, event
...

class Agent(Plugin):
    """Requires ``llm``; picks a provider by tag from its own config."""

    def __init__(self) -> None:
        super().__init__(
            name="agent",
            requires={"llm"},
            config_schema={
                "model_tag": ConfigField(str, REQUIRED, "tag of the llm provider to talk to"),
            },
        )
        self.lines = ["hello there", "what's the weather like?"]

    async def on_start(self) -> None:
        self.llm = await self.get_capability("llm", tag=self.config("model_tag"))
        await self.emit("turn")
```

`model_tag` uses `REQUIRED` instead of a default — not because `REQUIRED` is the norm,
but because there genuinely is no sane default once two providers exist. `suffix` above
has one obvious default; `model_tag` doesn't, so it's mandatory. Leave it out of
`host_configs()` and the plugin fails to start with a clear message naming the missing
key, not a silent fallback to whichever provider happened to load first.

`get_capability("llm", tag=self.config("model_tag"))` is the whole change: same
capability name as before, now narrowed by a tag pulled from config rather than
hardcoded. Nothing else in `agent.py` — the `speak` handler, the `agent.done` emit —
changes.

## `host.py`: `plugin_configs`

```python
def host_configs() -> dict[str, dict[str, object]]:
    """Per-plugin configuration, validated against each plugin's ``config_schema``."""
    return {
        "agent": {"model_tag": "prose"},  # which llm provider the agent asks for
    }


async def main() -> None:
    done = asyncio.Event()
    async with Core(plugin_configs=host_configs()) as core:  # starts/stops the kernel
        ...
```

`plugin_configs` is a `Core` constructor argument: a `{plugin_name: {key: value}}` map
handed to every plugin's `config_schema` at start time. This is the one point where the
host *does* mention a plugin by name — but only as configuration data keyed by string,
not as driving code that imports or calls anything. `host_configs()` doesn't create,
start, or reference the `Agent` class.

Validation happens in `start()`, so an unsatisfied `REQUIRED` field fails that plugin's
load with a `PluginError` naming the field and its description. Under `load_plugins` (as
`example_host/` uses — this `chat/` host still uses `load_plugins` too), that surfaces as
`BatchLoadError` with `phase="commit"`, unwinding nothing further since your own
`build_host` didn't yet install anything after the failing plugin. Try deleting the
`"agent"` entry from `host_configs()` and re-running to see it.

## Run it

```bash
python -m chat.host
```

Expected output — identical to tutorial 2's, since `model_tag` defaults to `"prose"`
here:

```text
user:  hello there
agent: Cheerfully #1: you said 'hello there'.
user:  what's the weather like?
agent: Cheerfully #2: you said 'what's the weather like?'.
persona: Cheerfully #3:
persona: Grumpily #4:
...[hot-reloaded persona]...
```

You'll also see a warning on stderr: `Capability already provided, adding provider`.
That's the default collision policy, `last_wins_with_warning`, firing because two
plugins now `provide={"llm"}` — tags don't exempt them from it. It's benign here since
the agent never resolves `"llm"` untagged, but it is why `error_on_conflict` and
`first_wins` exist as alternative policies for hosts that want a contested capability to
be a hard error instead.

**Try it yourself:** change `"model_tag": "prose"` to `"terse"` in `host_configs()` and
re-run:

```text
user:  hello there
agent: Cheerfully #1: hello there? noted.
user:  what's the weather like?
agent: Cheerfully #2: what's the weather like?? noted.
persona: Cheerfully #3:
persona: Grumpily #4:
...[hot-reloaded persona]...
```

Same persona, same reply count, different provider — only the config changed.

## The key idea

A capability name picks *what* — the shape a consumer needs. A tag picks *which* —
the specific instance among however many providers agree to answer that name. Config is
what lets that choice live outside the code: `agent.py` never hardcodes `"prose"` or
`"terse"`, `host_configs()` does, and swapping the answer is a data change, not a
redeploy.

## Next steps

Continue to [Deterministic conversations](deterministic-conversations.md): replace the
turn loop above with correlated request/reply, so the agent answers a specific question
instead of just whatever comes next.

`example_host/`'s `agent.py` takes this further still: `create_background_task`, and a
typed `Protocol` in place of the bare `"llm"` string — see
[`examples/example_host/`](https://github.com/hiddenfalls42/uxok/tree/main/examples/example_host)
for where this series ends up.

- [Capability system](../explanation/capability-system.md) — tags, collision policies,
  and the full resolution algorithm
- [Use tags for provider selection](../how-to/how-to-use-tags-for-provider-selection.md)
- [Use capability policies](../how-to/how-to-use-capability-policies.md) —
  `error_on_conflict`, `first_wins`, and the rest of `capability_collision`
