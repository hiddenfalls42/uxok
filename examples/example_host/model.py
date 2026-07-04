"""Model — provides the typed ``llm`` capability, in prose.

Stands in for an inference backend. It *provides* the ``llm`` capability
**typed**: ``provides={LLM}`` names a Protocol, so the kernel checks — at
admission, structurally, method by method — that this class actually implements
the contract it claims. The Protocol lives in this module and is *not* imported
by any consumer: the agent states its own copy of the contract and the two are
matched by shape and by the derived name (``LLM`` → ``"llm"``), never by import.

``tags={"prose"}`` discriminates this provider from ``terse_model.py``, which
provides the same capability — consumers pick a provider with
``get_capability(..., tag=...)``. The ``suffix`` config field shows an optional
setting with a default; the host may override it via ``plugin_configs``.
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
