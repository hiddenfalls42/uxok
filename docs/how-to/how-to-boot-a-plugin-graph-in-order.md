# Boot a plugin graph in order

A real host registers many plugins, and they depend on each other. The kernel validates `requires` at registration time, so the order you register in matters: a provider must be in the graph before any consumer that requires it. This guide covers deterministic boot order and the polling pattern for capabilities that arrive asynchronously.

## 1. Register providers before consumers

`core.register_plugin()` raises `MissingCapabilityError` if a plugin's `requires` set names a capability no registered plugin provides yet. Register along the dependency arrows — providers first, consumers last.

```python
import asyncio
from uxok import Core, Plugin


class Storage(Plugin):
    def __init__(self):
        super().__init__(name="storage", provides={"storage"})


class Indexer(Plugin):
    def __init__(self):
        super().__init__(name="indexer", requires={"storage"})

    async def on_start(self):
        self.storage = await self.get_capability("storage")


async def main():
    core = Core()
    await core.start()
    await core.register_plugin(Storage())   # provider first
    await core.register_plugin(Indexer())   # consumer second
    await core.stop()


asyncio.run(main())
```

Reversing the two `register_plugin` calls raises `MissingCapabilityError` on the `Indexer`, because `"storage"` is not yet in the graph.

## 2. Factor the boot sequence into one function

Keep the registration sequence in a single `build_host()` function rather than scattering it through `main()`. The running program and the test suite can then boot the identical graph, so they never drift.

```python
async def build_host(core: Core) -> None:
    await core.register_plugin(Storage())
    await core.register_plugin(Indexer())
```

## 3. Poll for a capability that arrives later

Not every capability is present at boot. A plugin may be hot-loaded after startup (see [Use hot reload](how-to-use-hot-reload.md)), or a provider may register before its own async setup makes it usable. For these, poll at the host level until the capability resolves, then register the consumer that requires it.

```python
async def wait_for_capability(core: Core, name: str, timeout: float = 5.0) -> bool:
    """Poll until `name` is resolvable, or give up after `timeout` seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            await core.get_capability(name)
            return True
        except Exception:
            await asyncio.sleep(0.05)
    return False
```

Use it to gate a later registration step on a capability that some earlier plugin loads in the background:

```python
if await wait_for_capability(core, "metrics", timeout=2.0):
    await core.register_plugin(Dashboard())   # requires {"metrics"}
else:
    raise RuntimeError("metrics provider never came up")
```

The poll loop catches the lookup exception and retries; once a provider registers `"metrics"`, the next `get_capability()` succeeds and the function returns `True`. The bounded timeout turns a missing dependency into an explicit failure instead of a hang.

See [Work with capabilities](how-to-work-with-capabilities.md) for the `requires`/`resolves` distinction, [Manage plugin dependencies](how-to-manage-plugin-dependencies.md) for hard plugin-to-plugin (UUID) dependencies, and [Shut down gracefully](how-to-shut-down-gracefully.md) for the other end of the host lifecycle.
