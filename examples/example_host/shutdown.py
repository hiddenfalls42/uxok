"""ShutdownHandler — graceful shutdown for a long-running host.

``on_start`` installs OS signal handlers, ``on_stop`` removes them;
:meth:`wait_for_shutdown` blocks until SIGINT/SIGTERM or any plugin emitting
``system.shutdown``. Signal handling is platform-specific: Unix uses
``loop.add_signal_handler`` (async-native); Windows uses ``signal.signal``
bridged onto the loop with ``call_soon_threadsafe``, since its handler fires on
the main thread, not the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from typing import TYPE_CHECKING

from uxok import Plugin, event

if TYPE_CHECKING:
    from uxok.protocols import Event as EventType

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    _SHUTDOWN_SIGNALS = [signal.SIGINT]
    if hasattr(signal, "SIGBREAK"):  # Ctrl+Break — present on CPython/Windows
        _SHUTDOWN_SIGNALS.append(signal.SIGBREAK)
else:
    _SHUTDOWN_SIGNALS = [signal.SIGINT, signal.SIGTERM]


class ShutdownHandler(Plugin):
    """Traps shutdown signals and the ``system.shutdown`` event; unblocks the host loop."""

    def __init__(self) -> None:
        super().__init__(name="shutdown_handler", provides={"shutdown_handling"})
        self._shutdown_event = asyncio.Event()

    async def on_start(self) -> None:
        loop = asyncio.get_running_loop()
        if sys.platform == "win32":
            for sig in _SHUTDOWN_SIGNALS:
                signal.signal(
                    sig,
                    lambda s, _f, _loop=loop: _loop.call_soon_threadsafe(
                        self._on_signal, signal.Signals(s)
                    ),
                )
        else:
            for sig in _SHUTDOWN_SIGNALS:
                loop.add_signal_handler(sig, self._on_signal, sig)

    async def on_stop(self) -> None:
        if sys.platform == "win32":
            for sig in _SHUTDOWN_SIGNALS:
                with contextlib.suppress(Exception):
                    signal.signal(sig, signal.SIG_DFL)
        else:
            loop = asyncio.get_running_loop()
            for sig in _SHUTDOWN_SIGNALS:
                with contextlib.suppress(Exception):
                    loop.remove_signal_handler(sig)

    def _on_signal(self, sig: signal.Signals) -> None:
        logger.info("Received %s — initiating shutdown", sig.name)
        asyncio.get_event_loop().create_task(
            self.emit("system.shutdown", {"source": "signal", "signal": sig.name})
        )
        # Set directly so wait_for_shutdown() unblocks immediately, before the bus
        # dispatches system.shutdown to subscribers.
        self._shutdown_event.set()

    @event("system.shutdown")
    async def _on_shutdown_event(self, ev: EventType) -> None:
        """Any plugin emitting ``system.shutdown`` also triggers shutdown."""
        source = ev.data.get("source", "unknown") if ev.data else "unknown"
        logger.info("Shutdown requested by: %s (via %s)", source, ev.name)
        self._shutdown_event.set()

    async def wait_for_shutdown(self) -> None:
        """Block until a shutdown signal or ``system.shutdown`` event is received."""
        await self._shutdown_event.wait()
