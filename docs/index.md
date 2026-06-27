# uxok

uxok is an async-first hot-loading plugin microkernel for Python. It provides exactly five primitives — an event bus, a hook system, a plugin registry, a capability system, and a base `Plugin` class — and nothing else. Everything a host application does is implemented as plugins that declare what they provide and what they require; the kernel wires them together without any plugin knowing about another directly.

The kernel was designed with a mashup of concepts and the name `uxok` spells that hybrid in miniature: `u` for micro, `xo` for exo and `k` for kernel, referencing the three borrowed architectures. By structure uxok is a microkernel, but its capability system follows the MIT exokernel `xok`'s discipline — *mechanism, not policy*, resources reached through secure bindings, abstraction pushed out into plugins. It stops short of the exokernel's hardware protection: plugins share one process and one trust domain. [Microkernel or exokernel?](explanation/architecture-overview.md#microkernel-or-exokernel) draws the line. 

Beyond those primitives, the core keeps time: it runs a single monotonic clock — `core.tick` — that every plugin can read, and any event or hook can be deferred to a precise future tick with `emit(..., at_tick=...)`. See [The tick system](explanation/tick-system.md). *It is worth mentioning that python is python, so "precise" is a relative term.* 

All design decisions are made with one goal in mind: creating a reliable and easy to maintain kernel that reduces boilerplate needed to build decentralized agentic applications with self-coding capability. This project started with the intention of making myself a quick library to use, but I got hooked and couldn't stop until I had a full blown microkernel. 

> uxok is a WIP and at this point experimental, especially the docs. If you spot some inconsistency or general nonsense, drop me an issue on github! 

## Quick start

```python
import asyncio
from uxok import Core, Plugin, event

class GreeterPlugin(Plugin):
    def __init__(self, core):
        super().__init__(core, provides={"greeter"})

    @event("greet.requested")
    async def on_greet(self, ev):
        print(f"Hello from {ev.source}")

async def main():
    core = Core()
    await core.start()
    await core.register_plugin(GreeterPlugin(core))
    await core.stop()

asyncio.run(main())
```

See [Getting started](tutorials/getting-started.md) for a complete walk-through that registers two dependent plugins and runs them end-to-end.

## Documentation

### [Tutorials](tutorials/index.md)

Step-by-step lessons for new users. Start here if you are learning uxok for the first time.

### [How-to guides](how-to/index.md)

Task-oriented guides for specific goals. Assumes you already have uxok running and want to accomplish one thing.

### [Explanation](explanation/index.md)

Architecture, design decisions, and concepts. Covers why the primitives are shaped the way they are and what tradeoffs that creates.

### [API reference](reference/uxok/index.md)

Complete API documentation for all public modules, classes, and functions. Generated from source docstrings.
