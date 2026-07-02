"""Model — a plugin that provides the ``llm`` capability and the ``persona`` hook.

Stands in for an inference backend. It *provides* the ``llm`` capability; any
plugin that declares ``requires={"llm"}`` calls :meth:`reply` through the
capability surface without ever importing this class. The ``persona`` hook lets
any plugin ask "what voice should replies use?" without knowing who answers.
"""

from __future__ import annotations

from uxok import Plugin, hook


class Model(Plugin):
    """Provides ``llm``: turns a prompt (plus a persona prefix) into a reply."""

    def __init__(self) -> None:
        super().__init__(name="model", provides={"llm"})

    async def reply(self, text: str, persona: str) -> str:
        return f"{persona} you said '{text}'."

    @hook("persona")
    async def voice(self) -> str:
        return "Cheerfully:"
