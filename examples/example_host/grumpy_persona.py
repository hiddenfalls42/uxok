"""A replacement Persona, hot-loaded from source at runtime.

Never booted — ``build_host`` excludes it; ``conductor.py`` (or the watcher, if
you edit this file while the program runs) hands its source to
``core.load_plugin``. It resolves to the same plugin name (``persona``) as
``persona.py``, so the kernel swaps it in and carries the reply count across
via ``get_state``/``restore_state``.
"""

from uxok import Plugin, hook


class Persona(Plugin):
    def __init__(self) -> None:
        super().__init__(name="persona")
        self._count = 0

    @hook("persona")
    async def voice(self) -> str:
        self._count += 1
        return f"Grumpily #{self._count}:"

    async def get_state(self) -> dict:
        return {"count": self._count}

    async def restore_state(self, state: dict) -> None:
        self._count = state.get("count", 0)
