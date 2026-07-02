"""host.py — a tiny hot-loader that composes the conversation and runs it.

A *host* boots a :class:`~uxok.Core` and brings plugins up on it. Rather than
importing the plugin classes, this host reads each plugin's *source* and hands it
to :meth:`~uxok.Core.load_plugin`; the kernel compiles, registers, and starts it.
That is uxok's "downloaded policy" — the kernel runs plugin code it never compiled
against, so the host binds to nothing but file paths and a load order, not to the
plugin classes themselves.

``build_host`` is shared by ``main`` and the test suite, so the running program
and the tested program never drift.

Run it:

    python -m examples.getting_started.host
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from uxok import Core

if TYPE_CHECKING:
    from uxok.protocols import Event

_HERE = Path(__file__).resolve().parent


async def build_host(core: Core) -> None:
    """Load the plugins from source, provider before requirer.

    ``model`` (provides ``llm``) must come up before ``agent`` (requires it) — the
    kernel checks ``requires`` at load time and would reject the agent otherwise.
    """
    for name in ("model", "agent"):
        path = _HERE / f"{name}.py"
        await core.load_plugin(path.read_text(), origin=str(path))


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
