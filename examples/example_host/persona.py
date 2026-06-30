"""Persona — contributes the agent's voice through the ``persona`` hook.

A deliberately tiny plugin, and the host's hot-reload target: it owns no
capability and holds no state, so swapping it at runtime changes the agent's
voice without touching the model or the agent. ``grumpy_persona.py`` is the
replacement the host loads live with ``core.load_plugin`` — same plugin name, so
the kernel performs a zero-downtime swap (cf. exokern-host hot-loading capability
contributors from disk).
"""

from __future__ import annotations

from uxok import Plugin, hook


class Persona(Plugin):
    """Answers the ``persona`` hook with the prefix the agent puts on each reply."""

    def __init__(self) -> None:
        super().__init__(name="persona")

    @hook("persona")
    async def voice(self) -> str:
        return "Cheerfully:"
