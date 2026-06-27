# Shut down gracefully

`core.stop()` tears the graph down cleanly — it unregisters every plugin, calling each `on_stop()`. The missing piece for a long-running host is *deciding when* to stop: trapping OS signals (Ctrl-C, `SIGTERM` from a process manager) and letting any plugin request shutdown over the bus. This guide builds a small `ShutdownHandler` plugin that does both and blocks `main()` until shutdown is requested.

## 1. Block the host until shutdown is requested

A host's `main()` boots the graph and then has to stay alive. Drive that wait from an `asyncio.Event` the handler sets when shutdown is requested.

```python
import asyncio
import signal
from uxok import Core, Plugin, event


class ShutdownHandler(Plugin):
    def __init__(self):
        super().__init__(name="shutdown_handler", provides={"shutdown_handling"})
        self._requested = asyncio.Event()

    async def wait_for_shutdown(self):
        await self._requested.wait()
```

## 2. Trap OS signals in `on_start`

Install signal handlers in `on_start()` and remove them in `on_stop()`, so the trap is live exactly while the plugin is. On Unix, `loop.add_signal_handler` is async-native.

```python
    async def on_start(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._request, sig.name)

    async def on_stop(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)

    def _request(self, source):
        self._requested.set()
```

Windows has no `add_signal_handler`. There, register with `signal.signal(sig, ...)` and bridge the callback onto the loop with `loop.call_soon_threadsafe`, since the handler fires off-loop. The `examples/example_host/shutdown.py` file in the repository shows the cross-platform version.

## 3. Let any plugin request shutdown over the bus

Signals are not the only trigger — a plugin that hits a fatal condition should be able to ask for a clean stop. Subscribe to a `system.shutdown` event and set the same `Event`, so both paths converge.

```python
    @event("system.shutdown")
    async def _on_event(self, ev):
        self._requested.set()
```

Any plugin can now trigger shutdown with `await self.emit("system.shutdown", {"source": self.metadata.name})`.

## 4. Wire it into the host loop

Register the handler with the rest of the graph, then `await wait_for_shutdown()`. The `async with core:` block guarantees `core.stop()` runs on the way out — draining the graph — whether shutdown came from a signal, the bus, or an exception.

```python
async def main():
    core = Core()
    async with core:
        shutdown = ShutdownHandler()
        await core.register_plugin(shutdown)
        # ... register the rest of the graph ...
        await shutdown.wait_for_shutdown()
    # core.stop() has now unregistered every plugin and run each on_stop()


asyncio.run(main())
```

For an in-flight-request drain window, give long-running work a bounded grace period — `await asyncio.wait_for(drain(), timeout=8.0)` — after `wait_for_shutdown()` returns and before leaving the `async with` block.

See [Manage core lifecycle](how-to-manage-core-lifecycle.md) for what `core.stop()` does to each plugin, [Boot a plugin graph in order](how-to-boot-a-plugin-graph-in-order.md) for the startup counterpart, and [Subscribe to events](how-to-subscribe-to-events.md) for the `@event` mechanics.
