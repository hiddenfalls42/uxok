"""host.py — boots a Core, loads every plugin in this folder, waits for shutdown.

Names no plugin in its own logic (``host_configs()`` is per-plugin config *data*,
not driving code — see ``conductor.py`` for what actually drives the demo).
Run as a module: ``python -m <package>.host``.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from uxok import BatchLoadError, Core

_HERE = Path(__file__).resolve().parent
_HOST_FILES = {"__init__.py", "host.py"}
_SWAP_PAYLOADS = {"grumpy_persona.py"}  # hot-reloaded in later; not part of the boot graph


def host_configs() -> dict[str, dict[str, object]]:
    """Per-plugin configuration, validated against each plugin's ``config_schema``."""
    return {
        "agent": {"model_tag": "prose"},  # which llm provider the agent asks for
        "watcher": {"watch_dir": str(_HERE)},  # REQUIRED — no default exists
    }


def _boot_sources() -> list[tuple[str, str]]:
    """The ``(code, origin)`` sources for this folder's boot graph.

    Excludes ``grumpy_persona.py`` (hot-reload payload — booting it would collide
    with ``persona.py``, two plugins named ``persona`` in one batch) and the
    host's own files.
    """
    skip = _HOST_FILES | _SWAP_PAYLOADS
    paths = [path for path in sorted(_HERE.glob("*.py")) if path.name not in skip]
    return [(path.read_text(), str(path)) for path in paths]


async def build_host(core: Core) -> tuple[str, ...]:
    """Load every plugin module in this folder; keep the whole graph or nothing.

    On failure, unwinds the installed prefix in reverse before re-raising.
    """
    try:
        return await core.load_plugins(_boot_sources())
    except BatchLoadError as e:
        for name in reversed(e.installed):  # () on a plan-phase fault → no-op
            await core.unregister_plugin(name)
        raise


async def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    async with Core(capability_access="sealed", plugin_configs=host_configs()) as core:
        await build_host(core)
        # The host holds no plugin instances — it resolves the shutdown handler
        # through the capability surface, the same door the plugins use. The
        # conductor plugin is already driving the demo conversation by now.
        shutdown = await core.get_capability("shutdown_handling")
        await shutdown.wait_for_shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
