"""Persona — contributes the agent's voice through the ``persona`` hook.

The hot-reload target: ``get_state``/``restore_state`` are the state-handoff
contract the kernel calls across a swap, so this plugin's reply count
survives being replaced by ``grumpy_persona.py``.
"""

from __future__ import annotations

from uxok import Plugin, hook


class Persona(Plugin):
    """Answers the ``persona`` hook with a counted prefix; count survives hot-swap."""

    def __init__(self) -> None:
        super().__init__(name="persona")
        self._count = 0

    @hook("persona")
    async def voice(self) -> str:
        self._count += 1
        return f"Cheerfully #{self._count}:"

    async def get_state(self) -> dict:
        return {"count": self._count}

    async def restore_state(self, state: dict) -> None:
        self._count = state.get("count", 0)
