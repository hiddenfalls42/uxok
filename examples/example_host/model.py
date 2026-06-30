"""Model — a provider plugin exposing the ``llm`` capability.

Stands in for an inference backend (cf. exokern-host's swappable inference
backends). It *provides* the ``llm`` capability; any plugin that declares
``requires={"llm"}`` calls :meth:`reply` through the capability surface without
ever importing this class. Swap this provider — or hot-reload it — and the agent
that consumes it is unchanged.
"""

from __future__ import annotations

from uxok import Plugin


class Model(Plugin):
    """Provides ``llm``: turns a prompt (plus a persona prefix) into a reply."""

    def __init__(self) -> None:
        super().__init__(name="model", provides={"llm"})

    async def reply(self, text: str, persona: str) -> str:
        return f"{persona} you said '{text}'."
