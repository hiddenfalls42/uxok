"""TerseModel — a second, competing provider of the ``llm`` capability.

Alongside ``model.py`` this makes ``llm`` a *contested* capability: both
providers live under the default ``last_wins_with_warning`` collision policy,
and consumers pick one with ``get_capability("llm", tag=...)``. The ``LLM``
Protocol here is this module's own copy of the contract, not imported from
``model.py``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from uxok import Plugin


@runtime_checkable
class LLM(Protocol):
    """This provider's statement of the ``llm`` contract."""

    async def reply(self, text: str, persona: str) -> str: ...


class TerseModel(Plugin):
    """Provides ``llm`` (typed), tagged ``terse``: answers in as few words as possible."""

    def __init__(self) -> None:
        super().__init__(
            name="terse_model",
            version="1.0.0",
            description="terse reply provider",
            provides={LLM},
            tags={"terse"},
        )

    async def reply(self, text: str, persona: str) -> str:
        return f"{persona} {text}? noted."
