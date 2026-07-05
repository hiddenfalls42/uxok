"""Agent — the conversational consumer of the ``llm`` capability.

Resolves its provider typed and tagged in ``on_start``
(``get_capability(LLM, tag=...)``); the tag comes from its own config, so the
model is chosen per deployment, not per code change. Answers each
``user.says`` on the cid-suffixed topic ``agent.says.<cid>``, in a background
task so the handler stays fast; ``has_subscribers`` skips the LLM call when
nobody's listening for that cid.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from uxok import ConfigField, Plugin, event

if TYPE_CHECKING:
    from uxok.protocols import Event as EventType


@runtime_checkable
class LLM(Protocol):
    """The agent's own statement of the contract it consumes."""

    async def reply(self, text: str, persona: str) -> str: ...


class Agent(Plugin):
    """Requires ``llm``; answers each ``user.says`` on its cid-correlated reply topic."""

    def __init__(self) -> None:
        super().__init__(
            name="agent",
            requires={LLM},
            hooks_consumed={"persona"},
            events_published={"agent.says.*"},
            config_schema={
                "model_tag": ConfigField(str, "prose", "tag of the llm provider to answer with"),
            },
        )

    async def on_start(self) -> None:
        # Typed + tagged resolution: the Protocol picks the contract, the tag
        # picks the provider. Under "sealed" this is a facet limited to `reply`.
        self.llm = await self.get_capability(LLM, tag=self.config("model_tag"))

    @event("user.says")
    async def respond(self, ev: EventType) -> None:
        cid = ev.data["cid"]
        # Handlers stay fast: the reply work runs as a tracked background task,
        # cancelled automatically if this plugin stops mid-conversation.
        await self.create_background_task(self._answer(cid, ev.data["text"]), name=f"answer-{cid}")

    async def _answer(self, cid: str, text: str) -> None:
        reply_topic = f"agent.says.{cid}"
        if not self.has_subscribers(reply_topic):
            return  # demand gate: nobody is waiting for this cid — skip the work
        # The persona is resolved per reply through the hook, so a hot-reloaded
        # persona is picked up immediately — no re-resolution needed here.
        persona = await self.hook("persona", firstresult=True)
        reply = await self.llm.reply(text, persona)
        print(f"agent: {reply}")  # noqa: T201 — demo output is the point
        await self.emit(reply_topic, {"text": reply})
