"""Agent — the conversational consumer of the ``llm`` capability.

The agent declares ``requires={LLM}`` with its *own* copy of the Protocol (it
never imports either model module) and resolves the provider **typed and
tagged** in ``on_start``: ``get_capability(LLM, tag=...)`` picks between the
competing providers by tag — the tag comes from the agent's own config, so the
host chooses the model per deployment, not per code change. Under
``capability_access="sealed"`` the typed resolution returns a protocol-limited
facet: the agent can call ``reply`` and nothing else.

Each ``user.says`` event carries a correlation id (``cid``); the handler stays
fast by pushing the actual work into ``create_background_task``, and the reply
is emitted on the cid-suffixed topic ``agent.says.<cid>`` — the host (or any
requester) awaits exactly its own answer on a glob subscription instead of
sleeping. ``has_subscribers`` demand-gates the work: no listener, no LLM call.
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
