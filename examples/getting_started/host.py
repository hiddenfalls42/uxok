"""host.py — a tiny hot-loader: it loads plugin files from this folder.

Rather than importing plugin classes or naming them in dependency order, it hands
every plugin module's source to ``core.load_plugins`` and lets the kernel work out
the load order from each plugin's declared capabilities. Run it as a module:
``python -m <package>.host``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from uxok import Core

if TYPE_CHECKING:
    from uxok.protocols import Event

_HERE = Path(__file__).resolve().parent
_HOST_FILES = {"__init__.py", "host.py"}


async def build_host(core: Core) -> None:
    """Load every plugin module in this folder, regardless of dependency order."""
    paths = [path for path in sorted(_HERE.glob("*.py")) if path.name not in _HOST_FILES]
    await core.load_plugins([(path.read_text(), str(path)) for path in paths])


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
