# uxok

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/hiddenfalls42/uxok/blob/main/LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![CI](https://github.com/hiddenfalls42/uxok/actions/workflows/ci.yml/badge.svg)](https://github.com/hiddenfalls42/uxok/actions/workflows/ci.yml)

> **Status: 0.x, pre-1.0.** Functional and well-tested, but the public API is still settling and may change between releases. If you build on it now, pin to a commit and expect to adjust on upgrade.

**A hot-loading microkernel for Python — minimal core, everything else is a plugin.**

```python
import asyncio
from uxok import Core, Plugin

# A plugin that PROVIDES a capability — a named service the kernel hands out.
class Model(Plugin):
    def __init__(self):
        super().__init__(name="model", provides={"llm"})

    async def complete(self, prompt):
        return f"(response to: {prompt})"

# A plugin that REQUIRES "llm" — it never imports Model, it asks the kernel.
class Agent(Plugin):
    def __init__(self):
        super().__init__(name="agent", requires={"llm"})

    async def on_start(self):
        llm = await self.core.get_capability("llm")  # kernel resolves the wiring by name
        print(await llm.complete("plan my day"))     # swap the model at runtime; the agent is unchanged

async def main():
    async with Core() as core:                # the host
        await core.register_plugin(Model())   # provider registers first
        await core.register_plugin(Agent())   # on_start resolves "llm" and runs

asyncio.run(main())
```

That `provides`/`requires`/`get_capability` loop is what sets uxok apart: plugins are wired by name, never by import, so any provider can be hot-swapped underneath its consumers while the process runs. There's real depth behind each piece — the [documentation](https://hiddenfalls42.github.io/uxok/) covers it with tutorials, how-to guides, architecture deep-dives, and a generated API reference.

I made this python kernel for prototyping self-coding agentic applications that interface with physical components, but it's not limited to that. It started as an experiment with early agentic coding to make a simple library and evolved into a personal passion — and into something that actually works.

## How it works

uxok has a microkernel-style architecture: the core provides only essential primitives for building extensible, self-modifying agentic applications — event-driven communication, hook-based extension points, hot-loading plugins with lifecycle management, and capability-based dependency resolution. Everything else is a plugin you can add, remove, or swap at runtime.

Reach for it when you need plugins you can swap *while the process is running*, not just load once at startup. A complete multi-plugin host lives in [`examples/`](https://github.com/hiddenfalls42/uxok/tree/main/examples).

## Install

Not on PyPI yet — install from the repo:

```bash
pip install git+https://github.com/hiddenfalls42/uxok.git
```

A virtualenv is strongly recommended.

## Features

- **Hot-Loading** — Add/remove/replace plugins at runtime without restart
- **Event-Driven** — Non-blocking pub/sub messaging for loose coupling
- **Hook System** — Priority-based extension points for pipelines
- **Capability System** — Kernel-style dependency resolution with tag-based selection
- **Frozen records** — Protocols and event/hook payloads are immutable at runtime; behavior changes by hot-swapping plugins, not by mutating live objects
- **Type Safety** — Full protocol-based typing with mypy support

## Development 

```bash
git clone https://github.com/hiddenfalls42/uxok.git
cd uxok
pip install -e .[dev]

pytest                       # Run tests
ruff check src tests examples  # Lint
mypy src                     # Type check
```

## Documentation

**Build locally:**
```bash
pip install -e ".[docs]"
mkdocs build --strict  # Output in ./site/
```
Use `mkdocs serve` to preview the site live instead of building it. The published docs live at <https://hiddenfalls42.github.io/uxok/>.

## Contributing

Contributions welcome! 

## The name

The kernel was designed with a mashup of concepts and the name `uxok` spells that hybrid in miniature: `u` for micro, `xo` for exo and `k` for kernel, referencing the three borrowed architectures. By structure uxok is a microkernel, but its capability system follows the MIT exokernel `xok`'s discipline — *mechanism, not policy*, resources reached through secure bindings, abstraction pushed out into plugins. It stops short of the exokernel's hardware protection: plugins share one process and one trust domain. [Microkernel or exokernel?](https://hiddenfalls42.github.io/uxok/explanation/architecture-overview/#microkernel-or-exokernel) draws the line.

The exokernel idea mostly lost in the OS world, but I think it has new life in the agentic era: the abstractions an agent needs aren't knowable in advance — which is exactly the problem exokernels were built for.

## License

MIT — see [LICENSE](https://github.com/hiddenfalls42/uxok/blob/main/LICENSE)
