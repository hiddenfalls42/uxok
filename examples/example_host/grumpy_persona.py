"""A replacement Persona the host hot-loads at runtime.

This module is never imported and never booted — ``build_host`` skips it and
``host.py`` (or the watcher, if you edit this file while the program runs)
hands its *source* to ``core.load_plugin``. Because the class resolves to the
same plugin name (``persona``) as ``persona.py``, the kernel performs a
zero-downtime swap — and hands the old instance's ``get_state()`` dict to this
instance's ``restore_state``, so the reply count keeps climbing across the
swap. That continuity is the proof the handoff happened.

``load_plugin`` only injects ``Plugin`` into the execution namespace and
instantiates the class with no arguments, so the code imports its own
decorators and keeps the constructor side-effect-free.
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
