# Manage plugin dependencies

Declare which plugins your plugin depends on to control registration order and prevent removal of plugins that others still need.

## 1. Declare dependencies in `super().__init__()`

Pass the set of dependency plugin UUIDs to the `dependencies` parameter when calling `super().__init__()`:

```python
from uxok import Plugin

class CachePlugin(Plugin):
    def __init__(self):
        super().__init__(name="cache")

class DatabasePlugin(Plugin):
    def __init__(self, cache_id):
        super().__init__(
            name="database",
            dependencies={cache_id},  # UUID of the cache plugin
        )
```

Dependencies are declared by plugin UUID, not by name. The registry validates that every declared dependency exists before allowing registration.

!!! note
    For most inter-plugin relationships, declare `requires` and `provides` capability strings instead. The capability system builds dependency edges automatically from those declarations and is the preferred approach. Use `dependencies` only when you need a hard plugin-to-plugin link that cannot be expressed through capabilities.

## 2. Register in dependency order

Register dependencies before dependents. The registry enforces this order at registration time:

```python
import asyncio
from uxok import Core, Plugin

class CachePlugin(Plugin):
    def __init__(self):
        super().__init__(name="cache")

class DatabasePlugin(Plugin):
    def __init__(self, cache_id):
        super().__init__(name="database", dependencies={cache_id})

async def main():
    core = Core()

    cache = CachePlugin()
    await core.register_plugin(cache)

    database = DatabasePlugin(cache.metadata.id)
    await core.register_plugin(database)

asyncio.run(main())
```

If you attempt to register `DatabasePlugin` before `CachePlugin`, registration fails immediately with `PluginError: dependency not found`.

## 3. Inspect plugins and their load order

Call `core.list()` to retrieve all registered plugins as a `PluginCollection` and inspect their positions:

```python
plugins = await core.list()

for view in plugins:
    print(f"{view.name}  load_order={view.load_order}")
    print(f"  provides: {view.provides}")
    print(f"  requires: {view.requires}")
```

`PluginView.load_order` is numbered from 1; dependencies always have a lower value than their dependents. See [use plugin collections](how-to-use-plugin-collections.md) for name-based lookup and live-instance retrieval.

## 4. Detect circular dependencies

The registry rejects dependency edges that would close a cycle. The guard runs at edge-installation time — both at initial registration and when edges are replaced during a hot reload. It raises `PluginError` identifying the plugin (by id) whose proposed edges would close the cycle; old edges are restored automatically.

Wrap `load_plugin()` or `register_plugin()` in `try/except` to handle rejection:

```python
from uxok import PluginError

try:
    await core.load_plugin(plugin_a_v2_code)
except PluginError as e:
    print(f"Caught: {e}")
    # PluginError: Circular dependency detected
    # The registry rolled back; the old plugin is still running.
```

## 5. Choose between `dependencies` and `requires`/`provides`

Use `dependencies` for a hard link to a specific plugin instance. Use `requires` and `provides` when any registered provider of a capability name will do:

```python
class StoragePlugin(Plugin):
    def __init__(self):
        super().__init__(
            name="storage",
            provides={"storage"},
        )

class AppPlugin(Plugin):
    def __init__(self):
        super().__init__(
            name="app",
            requires={"storage"},  # any "storage" provider satisfies this
        )
```

When `AppPlugin` registers, the capability system finds the registered `"storage"` provider and adds its UUID to `AppPlugin`'s dependency edges automatically. The registry then enforces the same ordering and removal guards as explicit `dependencies`. See [work with capabilities](how-to-work-with-capabilities.md) for the full capability workflow.

## 6. Unregister in dependent-first order

Unregistration fails if other plugins still depend on the plugin you are removing:

```python
from uxok import PluginError

try:
    await core.unregister_plugin("cache")
except PluginError as e:
    print(f"Cannot unregister: {e}")
    # PluginError: dependents present -> database
```

Unregister the dependent first, then the dependency:

```python
await core.unregister_plugin("database")
await core.unregister_plugin("cache")
```

`force=True` bypasses the dependent check. The framework uses it internally during hot reload and full teardown. Avoid it in application code — removing a plugin while its dependents are still running leaves those plugins with a stale dependency reference.
