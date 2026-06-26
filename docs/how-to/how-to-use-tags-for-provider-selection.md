# Use tags for provider selection

Tags let you pick one specific provider when several plugins declare the same capability. Instead of changing the global collision or selection policy, you annotate each provider with descriptive strings and pass a `tag` argument at call time to select among them.

## 1. Add tags to a plugin

Pass `tags` to the `Plugin` constructor. Tags are arbitrary strings — choose values that describe the provider's runtime characteristics:

```python
from uxok import Plugin

class LocalStorage(Plugin):
    def __init__(self) -> None:
        super().__init__(
            name="local_storage",
            provides={"storage"},
            tags={"local", "fast"},
        )

class RemoteStorage(Plugin):
    def __init__(self) -> None:
        super().__init__(
            name="remote_storage",
            provides={"storage"},
            tags={"remote", "durable"},
        )
```

Tags are stored in `PluginMetadata.tags` as a `frozenset[str]`. A provider can carry any number of tags.

## 2. Request a provider by tag

Pass `tag` to `get_capability()`. The capability system filters providers to those whose `tags` set contains the requested string, then applies the configured selection policy to that filtered list:

```python
class DataPlugin(Plugin):
    async def process_locally(self, data):
        storage = await self.get_capability("storage", tag="local")
        await storage.save(data)

    async def process_remotely(self, data):
        storage = await self.get_capability("storage", tag="remote")
        await storage.save(data)
```

## 3. Handle a tag miss

When no registered provider carries the requested tag, `get_capability()` raises `CapabilityError`. The error message names the requested capability and lists the available capability names, but does not include tag details — the tag-level mismatch is not preserved in the raised exception:

```python
from uxok import CapabilityError

try:
    storage = await self.get_capability("storage", tag="archive")
except CapabilityError as e:
    # CapabilityError: "Capability 'storage' not available.
    #   Available capabilities: storage
    #   Did you forget to register a plugin that provides 'storage'?"
    print(f"Tag not found: {e}")
```

The message reflects the exception the `Core` layer raises after catching the internal tag mismatch. Even though the `storage` capability itself is registered, the message reads as if it is not — because the tag filter result is indistinguishable from a missing capability at that layer. To diagnose which tags are available, inspect the capability providers directly (see step 5 below).

## 4. Combine tags with typed capabilities

Tag filtering composes with Protocol-based lookup. Pass the Protocol type instead of a string name to get IDE autocomplete and contract validation at registration time:

```python
from typing import Protocol

class StorageProtocol(Protocol):
    async def save(self, data): ...
    async def load(self, key: str): ...

class LocalStorage(Plugin):
    def __init__(self) -> None:
        super().__init__(
            provides={StorageProtocol},
            tags={"local"},
        )

# Protocol-typed lookup with tag filter
storage = await self.get_capability(StorageProtocol, tag="local")
await storage.save(data)
```

## 5. Inspect provider tags

Use `collection.capability.info(name)` to see which tags each registered provider declares. The `providers` list contains one dict per provider; each dict has keys `name`, `id`, `version`, `description`, and `tags`:

```python
plugins = await core.list()
info = plugins.capability.info("storage")  # CapabilityInfo | None

if info is not None:
    for provider in info.providers:
        print(f"{provider['name']}: {provider['tags']}")
    # local_storage: ['fast', 'local']
    # remote_storage: ['durable', 'remote']
```

`info` returns `None` when the capability name is unknown. Calling `info` before requesting by tag is a reliable way to confirm that the tag you need is present.

See [work with capabilities](how-to-work-with-capabilities.md) for basic capability usage and [use capability policies](how-to-use-capability-policies.md) for global collision and selection configuration.
