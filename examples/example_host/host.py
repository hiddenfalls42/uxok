"""host.py — composes the conversational example into a runnable program.

A *host* boots a :class:`~uxok.Core`, hands it a folder of plugin sources, and
keeps it alive. This is the extended sibling of the minimal ``getting_started/``
example — the same hot-loading host shape, grown the features a real program
leans on:

    build_host(core)       batch-load every plugin module via core.load_plugins
    core.load_plugin(...)  hot-swap the persona mid-conversation
    ShutdownHandler        trap signals + system.shutdown, drain cleanly

The graph it loads:

    user.says ──▶ Agent ──hook "persona"──▶ Persona   (hot-reloaded ──▶ grumpy)
                    │ requires "llm"
                    └──▶ Model

``build_host`` is shared by ``main`` and the test suite, so the running program
and the tested program never drift. Run it as a module:
``python -m <package>.host``.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from uxok import Core
from uxok.protocols import Event

_HERE = Path(__file__).resolve().parent
_HOST_FILES = {"__init__.py", "host.py"}
_SWAP_PAYLOADS = {"grumpy_persona.py"}  # hot-reloaded in later; not part of the boot graph

logger = logging.getLogger("example_host")


async def build_host(core: Core) -> None:
    """Load every plugin module in this folder, regardless of dependency order.

    ``core.load_plugins`` works out the commit order from each plugin's declared
    capabilities (``Model`` before the ``Agent`` that requires ``"llm"``), so the
    host names no plugin and no ordering. ``grumpy_persona.py`` is skipped: it is
    the hot-reload payload ``main`` loads live, and booting it here would collide
    with ``persona.py`` (two plugins named ``persona`` in one batch).
    """
    skip = _HOST_FILES | _SWAP_PAYLOADS
    paths = [path for path in sorted(_HERE.glob("*.py")) if path.name not in skip]
    await core.load_plugins([(path.read_text(), str(path)) for path in paths])


async def say(core: Core, text: str) -> None:
    """Put one user line on the bus and let the agent's reply settle.

    Event dispatch is fire-and-forget, so the brief sleep lets the agent's handler
    (and its nested ``agent.says`` emit) run before the next line.
    """
    print(f"user:  {text}")  # noqa: T201 — demo output is the point
    await core.events.publish(Event("user.says", {"text": text}))
    await asyncio.sleep(0.1)


async def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    async with Core() as core:  # context manager starts/stops the kernel
        await build_host(core)
        # The host holds no plugin instances — it resolves the shutdown handler
        # through the capability surface, the same door the plugins use.
        shutdown = await core.get_capability("shutdown_handling")

        await say(core, "hello there")

        # Hot-swap the persona from a sibling module's source — zero downtime, and
        # the agent's next reply speaks in the new voice without any change to it.
        grumpy = _HERE / "grumpy_persona.py"
        await core.load_plugin(grumpy.read_text(), origin=str(grumpy))
        print("...[hot-reloaded the persona]...")  # noqa: T201

        await say(core, "what's the weather like?")

        print("conversation done — Ctrl-C to exit")  # noqa: T201
        await shutdown.wait_for_shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
