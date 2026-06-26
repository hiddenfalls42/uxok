# Work with capabilities

## 1. Declare what your plugin provides

Pass a set of capability names to `provides` in the `Plugin` constructor.

```python
from uxok import Plugin

class StoragePlugin(Plugin):
    def __init__(self):
        super().__init__(
            name="storage",
            provides={"storage", "cache"},
        )

    async def save(self, key: str, value: object) -> None:
        ...
```

A plugin can provide any number of capabilities.

## 2. Declare what your plugin requires

Pass a set of capability names to `requires`. The kernel checks that every required capability is already registered before your plugin is allowed in.

```python
from uxok import Plugin, MissingCapabilityError

class DataPlugin(Plugin):
    def __init__(self):
        super().__init__(
            name="data",
            requires={"storage"},
        )
```

If no registered plugin provides `"storage"` at the time your plugin registers, `core.register_plugin()` raises `MissingCapabilityError` immediately. Register providers before consumers.

To authorize runtime access to a capability that is not a hard startup dependency, use `resolves` instead of `requires`. Names in `resolves` are never checked at registration — the provider can appear later.

```python
class DataPlugin(Plugin):
    def __init__(self):
        super().__init__(
            name="data",
            requires={"storage"},    # validated at registration; provider must exist
            resolves={"analytics"},  # runtime-only grant; no registration check
        )
```

Under `capability_access="declared"` or `"sealed"`, a plugin may only call `get_capability()` for names in its `requires | resolves` union. See [Lock down capability access](how-to-lock-down-capability-access.md) for the full secured-mode workflow.

## 3. Retrieve a capability at runtime

Call `await self.get_capability(name)` inside `on_start()` or any method that runs after your plugin is started.

```python
class DataPlugin(Plugin):
    def __init__(self):
        super().__init__(name="data", requires={"storage"})

    async def on_start(self) -> None:
        self.storage = await self.get_capability("storage")

    async def handle(self, key: str) -> None:
        await self.storage.save(key, "value")
```

`get_capability()` returns the plugin instance that provides the capability. Call its methods directly — there is no wrapper.

## 4. Use typed capabilities for IDE support

Pass a `Protocol` type instead of a string. The return value is typed as that protocol, so your IDE can autocomplete its methods.

```python
from typing import Protocol

class Storage(Protocol):
    async def save(self, key: str, value: object) -> None: ...

class DataPlugin(Plugin):
    async def on_start(self) -> None:
        # Returns Storage — IDE autocomplete works
        self.storage = await self.get_capability(Storage)
        await self.storage.save("key", "value")
```

The kernel derives the capability name from the class name (`Storage` → `"storage"`).

## 5. Handle a missing capability gracefully

By default, `get_capability()` raises `CapabilityError` when no provider is registered. Pass `capability_missing="return_none"` to `Core` to get `None` instead.

```python
from uxok import Core

core = Core(capability_missing="return_none")
```

Check the return value before calling methods on it.

```python
async def on_start(self) -> None:
    cache = await self.get_capability("cache")
    if cache is None:
        return  # Cache not available; continue without it
    self.cache = cache
```

The default is `"raise"`.

## 6. Filter providers by tag

When multiple plugins provide the same capability, pass `tag=` to select the one you want.

```python
class LocalCache(Plugin):
    def __init__(self):
        super().__init__(provides={"cache"}, tags={"local", "fast"})

class RemoteCache(Plugin):
    def __init__(self):
        super().__init__(provides={"cache"}, tags={"remote", "durable"})
```

```python
async def on_start(self) -> None:
    # Select the local provider
    self.cache = await self.get_capability("cache", tag="local")
```

If no registered provider carries that tag, `get_capability()` raises `CapabilityError`. See [use tags for provider selection](how-to-use-tags-for-provider-selection.md) for the full guide.

## 7. Configure collision and selection policies

See [use capability policies](how-to-use-capability-policies.md) for the three policy fields and their interactions.

## 8. Inspect capability providers

See [use plugin collections](how-to-use-plugin-collections.md) for the filtering and bulk-call API.
