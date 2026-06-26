# Use hot-reload to load and reload plugins at runtime

Hot-reload loads new plugins or replaces running ones without stopping the core. `core.load_plugin()` is the entry point for both paths — uxok detects whether a plugin with that name is already registered and routes accordingly.

## Load a plugin for the first time

1. Write your plugin as a code string containing exactly one `Plugin` subclass. `Plugin` is injected into the namespace; any other `uxok` names must be imported explicitly.

    ```python
    plugin_code = """
    from uxok import hook, event

    class DataProcessor(Plugin):
        async def on_start(self):
            print("DataProcessor started")

        @hook("data.process")
        async def process(self, data):
            return {"processed": True, **data}
    """
    ```

2. Create and start the core, then call `load_plugin()`.

    ```python
    from uxok import Core

    core = Core()
    await core.start()
    await core.load_plugin(plugin_code)
    ```

    uxok discovers the subclass, instantiates it, and calls `register_plugin()`.

## Reload a plugin in place

When the class name matches a running plugin, `load_plugin()` performs a zero-downtime swap instead of fresh registration.

1. Supply the updated source. The class name must be the same as the running plugin.

    ```python
    updated_code = """
    from uxok import hook

    class DataProcessor(Plugin):
        async def on_start(self):
            print("DataProcessor v2 started")

        @hook("data.process", priority=20)
        async def process(self, data):
            return {"processed": True, "version": 2, **data}
    """
    ```

2. Call `load_plugin()` again with the updated string.

    ```python
    await core.load_plugin(updated_code)
    ```

!!! note
    Keep plugin constructors side-effect-free and acquire resources in `on_start()`. The class is constructed once per `load_plugin()` call.

## Load a plugin that uses relative imports

If your plugin spans multiple files, pass the `origin` file path. uxok roots the isolated module at that path so relative imports resolve correctly.

```python
plugin_dir = "/path/to/my_plugin"
with open(f"{plugin_dir}/__init__.py") as f:
    code = f.read()
await core.load_plugin(code, origin=f"{plugin_dir}/__init__.py")
```

## Observe reload events

Subscribe to `core.plugin_reloaded` to react whenever a plugin is swapped. The event carries the plugin name, the old instance ID, and the new instance ID.

```python
from uxok import Plugin, event

class ReloadMonitor(Plugin):
    @event("core.plugin_reloaded")
    async def on_reload(self, evt):
        name = evt.data["plugin_name"]
        old_id = evt.data["old_id"]
        new_id = evt.data["new_id"]
        print(f"{name} reloaded ({old_id} -> {new_id})")
```

Register `ReloadMonitor` before any hot-reload calls so it is running when the first `core.plugin_reloaded` event fires.

## Observe capability changes

Subscribe to `core.capability.revoked` and `core.capability.rebound` to track what happens to the providers behind a capability when a plugin is swapped or removed. `core.capability.revoked` fires when a provider is unregistered with no replacement; `core.capability.rebound` fires during a hot-reload swap when a new provider takes over.

```python
from uxok import Plugin, event

class CapabilityMonitor(Plugin):
    @event("core.capability.revoked")
    async def on_revoked(self, evt):
        cap = evt.data["capability"]
        old_id = evt.data["old_provider_id"]
        print(f"{cap} revoked (was {old_id})")

    @event("core.capability.rebound")
    async def on_rebound(self, evt):
        cap = evt.data["capability"]
        old_id = evt.data["old_provider_id"]
        new_id = evt.data["new_provider_id"]
        print(f"{cap} rebound: {old_id} → {new_id}")
```

For how these events relate to sealed-mode facets and provider revocation detection, see [Secure capability access](../explanation/secure-capability-access.md).

## Handle errors during reload

**Mid-swap reload failure.** A failed `on_start()` rolls back the swap; the old plugin keeps running. The exception re-raises from `load_plugin()`.

```python
try:
    await core.load_plugin(updated_code)
except Exception as e:
    print(f"Reload failed, old version still running: {e}")
```

**Fresh-load failure.** A failed registration publishes `core.plugin_error` with `"phase": "register"` before re-raising. Subscribe to that event to observe first-load failures centrally.

**Post-swap `on_stop` failure.** If `on_stop()` raises on the old instance, uxok logs a warning and publishes `core.plugin_error` with `"phase": "on_stop"`. The swap still succeeds.

`load_plugin()` calls for the same name are serialized.

## Related pages

- [How to extend the plugin base class](how-to-extend-plugin-base.md) — constructor patterns and lifecycle hooks
- [How to execute hooks](how-to-execute-hooks.md) — registering hook handlers in a plugin
- [Plugin architecture](../explanation/plugin-architecture.md) — system-level view of the plugin model
