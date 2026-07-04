"""Agent — the conversational consumer of the ``llm`` capability.

The kernel half of the README quick-start, pulled into its own module. The agent
declares ``requires={"llm"}``, resolves that provider by name in ``on_start`` (it
never imports :class:`~examples.example_host.model.Model`), and answers every
``user.says`` event on the bus. Each reply's voice comes from the ``persona``
hook, so hot-reloading the persona changes how the agent speaks with no change
here — the agent is oblivious to which provider is live. Cf. exokern-host's
Assistant: event-driven, non-blocking, replies published back onto the bus.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from uxok import Plugin, event

if TYPE_CHECKING:
    from uxok.protocols import Event as EventType


class Agent(Plugin):
    """Requires ``llm``; turns each ``user.says`` into an ``agent.says`` reply."""

    def __init__(self) -> None:
        super().__init__(name="agent", requires={"llm"})

    async def on_start(self) -> None:
        # Resolved once by name; the capability surface hands back the live provider.
        self.llm = await self.get_capability("llm")

    @event("user.says")
    async def respond(self, ev: EventType) -> None:
        text = ev.data["text"]
        # The persona is resolved per reply through the hook, so a hot-reloaded
        # persona is picked up immediately — no re-resolution needed here.
        persona = await self.hook("persona", firstresult=True)
        reply = await self.llm.reply(text, persona)
        print(f"agent: {reply}")  # noqa: T201 — demo output is the point
        await self.emit("agent.says", {"text": reply})
