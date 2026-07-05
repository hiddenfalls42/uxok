"""Conductor — scripts the demo conversation: say, hot-swap the persona, say
again, report the roster. Hot-swaps through the same ``kernel.lifecycle`` facet
``watcher.py`` uses.

Runs the script in a background task rather than inline in ``on_start``: this
plugin commits before ``agent`` (pushed last by its ``requires={LLM}`` edge), so
an inline ``await`` on the first reply would deadlock ``load_plugins`` waiting on
a plugin that hasn't started yet in the same batch.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from uxok import ConfigField, CoreError, Plugin, PluginError, event

if TYPE_CHECKING:
    from uxok.protocols import Event as EventType

logger = logging.getLogger(__name__)

_GRUMPY = Path(__file__).resolve().parent / "grumpy_persona.py"


class Conductor(Plugin):
    """Scripts the demo conversation: say, hot-swap the persona, say again, report."""

    def __init__(self) -> None:
        super().__init__(
            name="conductor",
            requires={"kernel.lifecycle"},
            events_published={"user.says"},
            hooks_consumed={"roster.report"},
            config_schema={
                "autorun": ConfigField(bool, True, "run the scripted demo conversation on start"),
            },
        )
        self._cids = itertools.count(1)
        self._pending: dict[str, asyncio.Future[str]] = {}

    async def on_start(self) -> None:
        self._lifecycle = await self.get_capability("kernel.lifecycle")
        if self.config("autorun"):
            await self.create_background_task(self.run_script(), name="conductor-script")

    @event("agent.says.*")
    async def _on_reply(self, ev: EventType) -> None:
        """Resolve the future waiting on this reply's cid (the topic's last segment)."""
        future = self._pending.pop(ev.name.rsplit(".", 1)[-1], None)
        if future is not None and not future.done():
            future.set_result(ev.data["text"])

    async def _say(self, text: str) -> str:
        """Put one line on the bus and await its correlated reply — no sleeps."""
        cid = f"c{next(self._cids)}"
        self._pending[cid] = asyncio.get_running_loop().create_future()
        print(f"user:  {text}")  # noqa: T201 — demo output is the point
        await self.emit("user.says", {"cid": cid, "text": text})
        return await asyncio.wait_for(self._pending[cid], timeout=2.0)

    async def run_script(self) -> None:
        """Run the scripted demo conversation once: say, hot-swap, say, report.

        Public so ``autorun=False`` only skips the automatic run at start, not
        the capability itself.
        """
        await self._say("hello there")

        # get_state/restore_state carries the reply count across the swap.
        try:
            await self._lifecycle.load_plugin(_GRUMPY.read_text(), origin=str(_GRUMPY))
        except (PluginError, CoreError) as exc:
            logger.warning("conductor: persona hot-swap failed: %s", exc)
            return
        print("...[hot-reloaded the persona]...")  # noqa: T201

        await self._say("what's the weather like?")

        report = await self.hook("roster.report", firstresult=True)
        print(f"roster: {report}")  # noqa: T201

        print("conversation done — Ctrl-C to exit (edit grumpy_persona.py meanwhile!)")  # noqa: T201
