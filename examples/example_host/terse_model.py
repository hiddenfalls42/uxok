"""TerseModel — a second, competing provider of the ``llm`` capability.

Same capability name, different voice, different ``tags``. Registering it
alongside ``model.py`` makes ``llm`` a *contested* capability: under the default
``capability_collision="last_wins_with_warning"`` policy the kernel logs a
warning and lets both live, and consumers disambiguate with
``get_capability("llm", tag=...)``. The agent's config decides which tag it
asks for — swap the config, swap the model, no code change.

The ``LLM`` Protocol here is this module's own statement of the contract —
deliberately *not* imported from ``model.py``. Capability contracts are
structural: both sides declare the shape they mean, the kernel checks
providers against what they claim, and only strings and shapes ever cross a
module boundary.
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
