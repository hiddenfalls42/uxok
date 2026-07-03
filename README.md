# uxok

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/hiddenfalls42/uxok/blob/main/LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![CI](https://github.com/hiddenfalls42/uxok/actions/workflows/ci.yml/badge.svg)](https://github.com/hiddenfalls42/uxok/actions/workflows/ci.yml)

> **Status: 0.x, pre-1.0.** Functional and well-tested, but the public API is still settling and may change between releases. If you build on it now, pin to a commit and expect to adjust on upgrade.

**A hot-loading plugin microkernel for building self-modifying agentic systems in Python.**

Read the [docs](https://hiddenfalls42.github.io/uxok/) to learn more! 
> The docs are a WIP but I will be updating them actively: if you notice something off with them (or the code), please drop me an [issue](https://github.com/hiddenfalls42/uxok/issues).

## Installation
```bash
pip install uxok
```

## Quick start
Basic functionality in a single script. The same example as a modular host is in [`examples/example_host/`](https://github.com/hiddenfalls42/uxok/tree/main/examples/example_host).

```python
import asyncio
from uxok import Core, Plugin, event, hook

# Plugins are declared by subclassing `Plugin` and defining their name and what they provide in the capability metadata. 
class Model(Plugin):
    def __init__(self):
        super().__init__(name="model", provides={"llm"})

    # Methods are async so that other plugins can call them at random through capabilities.
    async def reply(self, text, persona):
        return f"{persona} you said '{text}'."

    # Hooks are set using a decorator and can be triggered by any plugin. 
    @hook("persona")
    async def voice(self):
        return "Cheerfully:"

# Other plugins can declare dependencies on other plugins' capabilities with "requires=". This builds a dependency graph, and tells the core which parts of itself to hand the plugin (a "core faucet"). 
class Agent(Plugin):
    def __init__(self, done):
        super().__init__(name="agent", requires={"llm"})
        self.lines = ["hello there", "what's the weather like?"]
        self.done = done
    
    # Capabilities can then be acquired from the "core faucet" object. 
    async def on_start(self):
        self.llm = await self.core.get_capability("llm")  # kernel wires it up
        await self.emit("turn")

    # Events are subscribed to in the same fashion as hooks. 
    @event("turn")
    async def speak(self, ev):
        if not self.lines:
            self.done.set()
            return
        line = self.lines.pop(0)
        persona = await self.hook("persona", firstresult=True)
        print(f"user:  {line}")
        print(f"agent: {await self.llm.reply(line, persona)}")
        await self.emit("turn")

# The main program just opens a core and loads plugins onto it; registration order matters, providers first.
async def main():
    done = asyncio.Event()
    async with Core() as core:                # async context manager starts/stops the kernel
        await core.register_plugin(Model())   # provider first
        await core.register_plugin(Agent(done))
        await done.wait()

asyncio.run(main())
```

## Features

- [**Hot-Loading**](https://hiddenfalls42.github.io/uxok/how-to/how-to-use-hot-reload/) — Add/remove/replace plugins at runtime without restart
- [**Capability System**](https://hiddenfalls42.github.io/uxok/explanation/capability-system/) — Kernel-style dependency resolution with tag-based selection
- [**Event-Driven**](https://hiddenfalls42.github.io/uxok/explanation/event-system/) — Non-blocking pub/sub messaging for loose coupling
- [**Hook System**](https://hiddenfalls42.github.io/uxok/explanation/hook-system/) — Priority-based extension points for pipelines
- [**Introspectable**](https://hiddenfalls42.github.io/uxok/how-to/how-to-use-plugin-collections/) — Query the live plugin graph at runtime: filter by capability, hook, event, or status
- [**Type Safety**](https://hiddenfalls42.github.io/uxok/explanation/architecture-overview/) — Full protocol-based typing with mypy support

uxok has a microkernel-style architecture: the core provides only essential primitives for building extensible, self-modifying agentic applications — event-driven communication, hook-based extension points, hot-loading plugins with lifecycle management, and capability-based dependency resolution. Everything else is a plugin you can add, remove, or swap at runtime.

## Development 

```bash
git clone https://github.com/hiddenfalls42/uxok.git
cd uxok
pip install -e .[dev]

pytest                       # Run tests
ruff check src tests examples  # Lint
mypy src                     # Type check
```

## Contributing

Contributions welcome! 


## License

MIT — see [LICENSE](https://github.com/hiddenfalls42/uxok/blob/main/LICENSE)
