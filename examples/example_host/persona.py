"""Persona — contributes the agent's voice through the ``persona`` hook.

The host's hot-reload target — and, unlike the tutorial's first version, it is
now *stateful*: it counts the replies it has voiced. ``get_state`` /
``restore_state`` are the hot-reload state-handoff contract: when the host
swaps in ``grumpy_persona.py`` the kernel calls ``get_state()`` on this
instance and ``restore_state(state)`` on the replacement, so the count
survives the swap — the very next reply says so.
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
