# Register and manage plugins

Register plugins with the core, unregister them cleanly, and look up any
registered plugin by ID, name, or capability.

## Register a plugin

1. Define a `Plugin` subclass.

```python
from uxok import Core, Plugin

class DataProcessor(Plugin):
    def __init__(self):
        super().__init__(
            name="data_processor",
            provides={"processing"},
        )

    async def on_start(self):
        print("DataProcessor started")
```

2. Create a `Core` instance, start it, and register the plugin.

```python
core = Core()
await core.start()
plugin = DataProcessor()
await core.register_plugin(plugin)
```

`register_plugin` validates dependencies, registers declared capabilities,
calls the plugin's `on_start()` handler, and fires the `plugin.registered`
hook. The core must be `RUNNING` — call `core.start()` (or use
`async with Core() as core:`) before registering plugins.

!!! note
    `plugin.registered` and `plugin.unregistered` are hook extension points,
    not events. Connect to them with `core.hooks.register(...)`, not with the
    `@event` decorator — an `@event("plugin.registered")` handler will never
    fire. See [register hook handlers](how-to-register-hook-handlers.md) for details.

## Unregister a plugin

3. Call `core.unregister_plugin()` with the plugin's UUID or its name.

```python
# By name
await core.unregister_plugin("data_processor")

# By UUID
await core.unregister_plugin(plugin.metadata.id)
```

Unregistration calls the plugin's `on_stop()` handler, removes it from the
registry, releases its capabilities, and fires the `plugin.unregistered`
hook. The operation raises `PluginError` if other registered plugins declare
this plugin as a dependency — remove or unregister dependents first.

## Get a single plugin

4. Fetch a live plugin instance by UUID or name with `core.get_plugin()`.

```python
instance = await core.get_plugin("data_processor")
if instance is not None:
    print(instance.metadata.name)
```

`get_plugin` returns `None` when no plugin with that identifier is registered.
It accepts a UUID object, a UUID string, or a plain name string.

## List all plugins

5. Call `core.list()` to get a snapshot of all registered plugins.

```python
plugins = await core.list()
print(f"Registered: {plugins.count}")
```

`core.list()` returns a `PluginCollection`. The collection rebuilds only when
the registry changes, so repeated calls between registrations are cheap.

6. Iterate over the collection to inspect each plugin.

```python
for view in plugins:
    print(f"{view.name}: ready={view.ready}, provides={view.provides}")
```

Each item is a `PluginView` with descriptive fields (`name`, `provides`,
`requires`) computed at snapshot time and benign live reads (`ready`,
`uptime`) that observe the actual plugin state. A `PluginView` is a
description, not a handle: it gives you no way to invoke a method on, or hand
back, the live instance. To *act on* a plugin, resolve it through the
`kernel.lifecycle` grant (`get_plugin`) or a typed capability.

## Look up a plugin in a collection

7. Find a plugin by name.

```python
view = plugins.by_name("data_processor")
if view and view.ready:
    print(f"{view.name} is active, up {await view.uptime():.1f}s")
```

8. Find a plugin by UUID.

```python
view = plugins.by_id(plugin.metadata.id)
```

Both methods return `None` when the plugin is absent. The view tells you a
plugin exists and what it declares; to invoke it, obtain the live instance via
the `kernel.lifecycle` grant or resolve the capability it provides.

## Filter plugins by capability

9. Get all plugins providing a given capability.

```python
storage_views = plugins.capability.provides("storage")
for view in storage_views:
    print(view.name)
```

10. Get all plugins that require a given capability.

```python
dependents = plugins.capability.consumes("storage")
print(dependents.names)
```

Capability filters return a new `PluginCollection`, so you can chain further
operations. For advanced filtering and the full `PluginCollection` API, see
[use plugin collections](how-to-use-plugin-collections.md).

## Handle dependency order

When one plugin depends on another, register the dependency first. The registry
validates that every declared dependency exists and rejects circular dependency
graphs at registration time with `PluginError`.

```python
await core.register_plugin(storage_plugin)
await core.register_plugin(DataProcessor())  # declares dependency on storage_plugin
```

Unregistration enforces the reverse: remove dependents before dependencies.
For a complete guide to declaring and managing plugin dependencies, see
[manage plugin dependencies](how-to-manage-plugin-dependencies.md).

## Shut down all plugins

11. Call `core.stop()` to unregister every plugin and shut down the core.

```python
await core.stop()
```

`core.stop()` unregisters plugins in reverse dependency order — dependents
before dependencies — so each plugin's `on_stop()` handler can still access
the capabilities it declared as requirements. After `stop()` completes, the
registry is empty and the core can be restarted with a fresh plugin graph.

For the full core lifecycle, see
[manage core lifecycle](how-to-manage-core-lifecycle.md).
