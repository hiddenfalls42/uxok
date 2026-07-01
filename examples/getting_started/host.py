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

    The provider (``Model``, which provides ``llm``) comes up before the
    ``Agent`` that requires it — registration order matters, providers first.
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
