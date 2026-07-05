"""Model — provides the typed ``llm`` capability, in prose.

``provides={LLM}`` names a Protocol, checked structurally at admission — never
imported by consumers, matched by shape and by the derived name (``LLM`` →
``"llm"``). ``tags={"prose"}`` distinguishes it from ``terse_model.py``, the
other ``llm`` provider.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from uxok import ConfigField, Plugin


@runtime_checkable
class LLM(Protocol):
    """The contract a reply provider satisfies (structural — never imported)."""

    async def reply(self, text: str, persona: str) -> str: ...


class Model(Plugin):
    """Provides ``llm`` (typed): turns a prompt plus a persona prefix into a reply."""

    def __init__(self) -> None:
        super().__init__(
            name="model",
            version="1.0.0",
            description="prose reply provider",
            provides={LLM},
            tags={"prose"},
            config_schema={
                "suffix": ConfigField(str, ".", "sentence-final punctuation"),
            },
        )

    async def reply(self, text: str, persona: str) -> str:
        return f"{persona} you said '{text}'{self.config('suffix')}"
