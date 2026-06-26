# Use plugin collections for advanced querying

`core.list()` returns a `PluginCollection` — a snapshot of all registered plugins
with pre-built indexes for fast querying. Use it when you need to filter, inspect,
or call across multiple plugins at once.

## Get the collection

1. Call `core.list()` after starting the core.

```python
from uxok import Core

core = Core()
await core.start()

plugins = await core.list()
print(plugins.names)   # ['auth', 'database', 'cache']
print(plugins.count)   # 3
```

## Filter by capability

2. Use `collection.capability.provides()` to find plugins that advertise a capability,
   or `collection.capability.consumes()` to find plugins that declare a requirement.

```python
# Plugins that provide the "storage" capability
storage_providers = plugins.capability.provides("storage")

# Plugins that require the "database" capability
database_consumers = plugins.capability.consumes("database")

# Inspect the first match descriptively
view = storage_providers.first()
if view:
    print(view.name, view.provides)
```

A `PluginView` is a description, not a handle — it has no `get_object()`/`call()`.
To resolve a provider to a live, usable object, request the capability itself:

```python
storage = await self.get_capability("storage")  # requires {"storage"} under enforcement
```

## Filter by hook or event

3. Use `.hook` and `.event` the same way: `.provides()` for plugins that register
   the point, `.consumes()` for plugins that listen to it.

```python
# Plugins that register the "authenticate" hook
auth_hook_owners = plugins.hook.provides("authenticate")

# Plugins subscribed to "system.shutdown" events
shutdown_listeners = plugins.event.consumes("system.shutdown")
```

## Narrow to active plugins

4. Prepend `.active` to restrict a query to plugins currently in the `"active"` state.

```python
active_storage = plugins.active.capability.provides("storage")
```

`.active` is a synchronous property — no await needed.

## Filter by uptime

5. Call `await collection.uptime_over(seconds)` to keep only plugins that have been
   running longer than the given threshold. Stale plugins (torn down since the snapshot
   was taken) are silently excluded.

```python
long_running = await plugins.active.uptime_over(60)
```

## Look up a plugin by name or ID

6. Use `by_name()` or `by_id()` for direct O(1) lookup against the root collection's
   pre-built indexes. Both return a `PluginView` or `None`.

```python
view = plugins.by_name("auth")
if view and view.ready:
    print(f"{view.name} is active")

view = plugins.by_id(some_uuid)
```

The view tells you a plugin exists and what it declares; to invoke it, obtain the
live instance via the `kernel.lifecycle` grant (`get_plugin`) or resolve the
capability it provides.

## Inspect a view's metadata

7. Read descriptive fields on any `PluginView` to understand what a plugin declares
   without loading it.

```python
from uxok import StalePluginError

for view in plugins:
    print(view.name, view.provides, view.requires)
    print(view.hooks_provided, view.events_published)
    print("ready:", view.ready)

    try:
        print(f"uptime: {await view.uptime():.1f}s")
    except StalePluginError:
        print("(plugin gone)")
```

!!! warning
    `view.uptime()` raises `StalePluginError` when the plugin was unregistered after
    the collection was fetched. Always catch it.

## Introspect capability providers

8. Use `collection.capability.info(name)` to get the full provider picture for a
   capability: who provides it, how many providers exist, which one is currently
   selected, and whether the capability was registered with a Protocol type.

```python
from uxok.registry import CapabilityInfo

info = plugins.capability.info("storage")
if info:
    print(info.selected_provider)
    print([p["name"] for p in info.providers])
    print(info.typed, info.protocol_name)
```

## Discover every capability in the collection

9. Read `collection.capabilities` for a sorted, deduplicated list of every capability
   name provided by any plugin in the collection.

```python
print(plugins.capabilities)  # ['cache', 'database', 'logging', 'storage']
```

## Act on the plugins you discovered

A collection is for *discovery*, not invocation — `PluginView` exposes no way to call into or hand back a live instance. Once you have found the plugins you want, act on them through an explicit authority:

10. To invoke methods on a discovered plugin, resolve it to a live instance through the
    `kernel.lifecycle` grant (declare `requires={"kernel.lifecycle"}`), then iterate:

```python
lifecycle = await self.get_capability("kernel.lifecycle")
for view in plugins.active.capability.provides("health"):
    plugin = await lifecycle.get_plugin(view.name)
    if plugin is not None:
        await plugin.health_check()
```

For most cases, prefer resolving the capability a plugin provides
(`await self.get_capability("storage")`) over reaching for the instance directly.
For a complete walkthrough of the lifecycle grant, see [Resolve a live plugin instance](how-to-resolve-a-live-plugin-instance.md).

For basic plugin registration, see
[register and manage plugins](how-to-register-and-manage-plugins.md). For capability
concepts, see the [capability system explanation](../explanation/capability-system.md).
