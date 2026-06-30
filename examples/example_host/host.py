"""host.py — composes the conversational example into a runnable program.

A *host* boots a :class:`~uxok.Core`, registers a graph of plugins on it, and
keeps it alive. This is the modular, extended sibling of the README quick-start:
the same Model / Agent / persona-hook conversation, but each plugin lives in its
own module and the host exercises the kernel features a real program leans on:

    build_host(core)       register the graph in dependency order
    core.load_plugin(...)  hot-swap the persona mid-conversation
    ShutdownHandler        trap signals + system.shutdown, drain cleanly

The graph it builds:

    user.says ──▶ Agent ──hook "persona"──▶ Persona   (hot-reloaded ──▶ grumpy)
                    │ requires "llm"
                    └──▶ Model

``build_host`` is shared by ``main`` and the test suite, so the running program
and the tested program never drift.

Run it:

    python -m examples.example_host.host
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from examples.example_host.agent import Agent
from examples.example_host.model import Model
from examples.example_host.persona import Persona
from examples.example_host.shutdown import ShutdownHandler
from uxok import Core
from uxok.protocols import Event

_HERE = Path(__file__).resolve().parent
logger = logging.getLogger("example_host")


async def build_host(core: Core) -> ShutdownHandler:
    """Register the conversation graph on ``core`` and return its ShutdownHandler.

    Registration follows the dependency arrows: the providers (``Model`` for the
    ``llm`` capability, ``Persona`` for the ``persona`` hook) come up before the
    ``Agent`` that consumes them. The ShutdownHandler is registered last so it
    traps signals only once the graph is live.
    """
    await core.register_plugin(Model())  # provides "llm"
    await core.register_plugin(Persona())  # contributes the "persona" hook
    await core.register_plugin(Agent())  # requires "llm"
    shutdown = ShutdownHandler()
    await core.register_plugin(shutdown)
    return shutdown


async def say(core: Core, text: str) -> None:
    """Put one user line on the bus and let the agent's reply settle.

    Event dispatch is fire-and-forget, so the brief sleep lets the agent's handler
    (and its nested ``agent.says`` emit) run before the next line.
    """
    print(f"user:  {text}", file=sys.stderr)  # noqa: T201 — demo output is the point
    await core.events.publish(Event("user.says", {"text": text}))
    await asyncio.sleep(0.1)


async def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    core = Core()
    async with core:
        shutdown = await build_host(core)

        await say(core, "hello there")

        # Hot-swap the persona from a sibling module's source — zero downtime, and
        # the agent's next reply speaks in the new voice without any change to it.
        grumpy = (_HERE / "grumpy_persona.py").read_text()
        await core.load_plugin(grumpy, origin=str(_HERE / "grumpy_persona.py"))
        print("...[hot-reloaded the persona]...", file=sys.stderr)  # noqa: T201

        await say(core, "what's the weather like?")

        print("conversation done — Ctrl-C to exit", file=sys.stderr)  # noqa: T201
        await shutdown.wait_for_shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
