"""host.py — a tiny hot-loader: it loads the two plugins from source and runs them.

Rather than importing the plugin classes, it hands each plugin's source to
``core.load_plugin`` and lets them coordinate by capability and event. Run it with
``python -m examples.getting_started.host``.
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
    """Load the plugins from source — provider (``model``) before requirer (``agent``)."""
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
