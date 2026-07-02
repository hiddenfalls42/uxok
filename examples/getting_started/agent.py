"""Agent — a plugin that requires the ``llm`` capability and drives the conversation.

Declares ``requires={"llm"}`` and resolves that capability by name in
``on_start`` — it never imports the sibling ``model`` module. It drives a short,
self-sustaining conversation over the event bus: each ``turn`` speaks one queued
line, then re-emits ``turn`` for the next. When the queue empties it emits
``conversation.over``, which the host listens for so it can shut down. It takes no
constructor arguments, so a host can hot-load it from source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from uxok import Plugin, event

if TYPE_CHECKING:
    from uxok.protocols import Event


class Agent(Plugin):
    """Requires ``llm``; speaks each queued line, then announces it is done."""

    def __init__(self) -> None:
        super().__init__(name="agent", requires={"llm"})
        self.lines = ["hello there", "what's the weather like?"]

    async def on_start(self) -> None:
        # Resolved once by name; the capability surface hands back the live provider.
        self.llm = await self.get_capability("llm")
        await self.emit("turn")

    @event("turn")
    async def speak(self, _ev: Event) -> None:
        if not self.lines:
            await self.emit("conversation.over")  # let the host shut down
            return
        line = self.lines.pop(0)
        # The persona is resolved per reply through the hook, so a different
        # provider's voice is picked up immediately — no re-resolution here.
        persona = await self.hook("persona", firstresult=True)
        print(f"user:  {line}")  # noqa: T201 — demo output is the point
        print(f"agent: {await self.llm.reply(line, persona)}")  # noqa: T201
        await self.emit("turn")  # re-arm the loop for the next line
