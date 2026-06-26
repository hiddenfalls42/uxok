# Resolve a live plugin instance

`core.list()` returns descriptive `PluginView` snapshots — they tell you what plugins exist and what they declare, but they carry no invocation path. To act on a specific plugin by calling its methods directly, resolve a live instance through the `kernel.lifecycle` grant.

## 1. Declare the grant

Add `"kernel.lifecycle"` to your plugin's `requires`. The kernel provides it as a reserved grant, so no plugin needs to register a provider and the declaration never fails admission.

```python
from uxok import Plugin

class Inspector(Plugin):
    def __init__(self):
        super().__init__(
            name="inspector",
            requires={"kernel.lifecycle"},
        )
```

## 2. Resolve the grant at runtime

Call `self.get_capability("kernel.lifecycle")` in `on_start()` or any method that runs after the plugin has started. It returns a `LifecycleFacet` exposing four graph-control methods: `register_plugin()`, `unregister_plugin()`, `load_plugin()`, and `get_plugin()`.

```python
async def on_start(self) -> None:
    self.lifecycle = await self.get_capability("kernel.lifecycle")
```

## 3. Obtain the live instance

Call `await self.lifecycle.get_plugin(name_or_id)` with the plugin name or UUID. It returns the live plugin object, or `None` if no plugin with that name is registered.

```python
async def check_storage(self) -> None:
    storage = await self.lifecycle.get_plugin("storage_provider")
    if storage is not None:
        await storage.run_diagnostics()
```

!!! note
    Prefer resolving capabilities over resolving instances directly. If the plugin you want exposes a capability, `await self.get_capability("storage")` is simpler and survives hot-reload transparently. Use `kernel.lifecycle` when you specifically need the instance — for administrative tasks, supervision, or iterating across plugins discovered via `core.list()`.

## Runnable example

```python
import asyncio
from uxok import Core, Plugin

class StoragePlugin(Plugin):
    def __init__(self):
        super().__init__(name="storage_plugin", provides={"storage"})

    async def ping(self) -> str:
        return "storage ok"

class Inspector(Plugin):
    def __init__(self):
        super().__init__(name="inspector", requires={"kernel.lifecycle"})

    async def on_start(self) -> None:
        lifecycle = await self.get_capability("kernel.lifecycle")
        instance = await lifecycle.get_plugin("storage_plugin")
        if instance is not None:
            print(await instance.ping())  # prints: storage ok

async def main() -> None:
    core = Core()
    await core.start()
    await core.register_plugin(StoragePlugin())
    await core.register_plugin(Inspector())
    await core.stop()

asyncio.run(main())
```

## Related pages

- [Secure capability access](../explanation/secure-capability-access.md) — How kernel.lifecycle fits into the declared/sealed access model
- [Use plugin collections](how-to-use-plugin-collections.md) — Discover plugins by capability, hook, or event before resolving an instance
- [Work with capabilities](how-to-work-with-capabilities.md) — Resolve a provider by capability name — simpler when you don't need the instance itself
