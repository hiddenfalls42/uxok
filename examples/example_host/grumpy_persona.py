"""A replacement Persona the host hot-loads at runtime.

This module is never imported by the package — ``host.py`` reads its *source* and
hands it to ``core.load_plugin``, exactly as a host would load plugin code pulled
from disk, a database, or a network (cf. exokern-host's plugin loader). Because
the class name resolves to the same plugin name (``persona``) as ``persona.py``,
the kernel swaps it in with zero downtime.

``load_plugin`` only injects ``Plugin`` into the execution namespace and
instantiates the class with no arguments, so the code imports its own decorators
and keeps the constructor side-effect-free.
"""

from uxok import Plugin, hook


class Persona(Plugin):
    def __init__(self) -> None:
        super().__init__(name="persona")

    @hook("persona")
    async def voice(self) -> str:
        return "Grumpily:"
