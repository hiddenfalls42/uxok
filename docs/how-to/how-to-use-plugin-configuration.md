# Use plugin configuration values

`self.config()` reads a configuration value for your plugin. It resolves through four sources in a fixed order, so you can always rely on a sensible fallback.

## 1. Read a value with a direct fallback

Call `self.config(key, default)` from any plugin method. The second argument is the value returned when none of the earlier sources matches:

```python
from uxok import Plugin

class WorkerPlugin(Plugin):
    def __init__(self):
        super().__init__(name="worker")

    async def on_start(self):
        concurrency = self.config("concurrency", 4)
        timeout     = self.config("timeout", 30.0)
        print(f"Starting {concurrency} workers, timeout={timeout}s")
```

## 2. Supply plugin-scoped values via `plugin_configs`

Pass a `plugin_configs` dictionary to `Core`. The top-level key is the plugin name; the inner dictionary contains field names and their values:

```python
from uxok import Core

core = Core(
    plugin_configs={
        "worker": {
            "concurrency": 8,
            "timeout": 60.0,
        }
    }
)
```

When `self.config("concurrency")` runs inside `WorkerPlugin`, it finds `8` here before ever reaching the fallback.

## 3. Use a declared schema to provide typed defaults

If you have declared a `config_schema`, `self.config()` returns the schema default when a value is absent from `plugin_configs`. You can then call `self.config(key)` with no fallback argument:

```python
from uxok import Plugin, ConfigField, REQUIRED

class CachePlugin(Plugin):
    def __init__(self):
        super().__init__(
            name="cache",
            config_schema={
                "redis_url": ConfigField(str, default="redis://localhost:6379", description="Redis server URL"),
                "ttl":       ConfigField(int, default=3600, description="Default TTL in seconds"),
                "namespace": ConfigField(str, REQUIRED, description="Key namespace prefix"),
            },
        )

    async def on_start(self):
        url       = self.config("redis_url")   # schema default if not supplied
        ttl       = self.config("ttl")         # schema default if not supplied
        namespace = self.config("namespace")   # always supplied — it is REQUIRED
        print(f"Cache ready at {url}, namespace={namespace}, ttl={ttl}")
```

Schema defaults sit between the plugin-scoped dictionary and the `CoreConfig` fallback. Supplying a value in `plugin_configs` overrides the schema default; not supplying one lets the schema default take effect.

## 4. Access `CoreConfig` attributes as a last resort

When a key is not found in `plugin_configs` and not declared in the schema, `self.config()` looks for an attribute of the same name on `CoreConfig`. This lets plugins read framework-wide settings without importing `CoreConfig` directly:

```python
from uxok import Plugin

class MonitorPlugin(Plugin):
    def __init__(self):
        super().__init__(name="monitor")

    async def on_start(self):
        # tick_rate is a CoreConfig attribute, not a plugin-scoped value
        rate = self.config("tick_rate", 1000)
        print(f"Monitoring at {rate} ticks/s")
```

Use this sparingly. Plugin behaviour that depends on framework-wide settings is harder to test in isolation.

## Lookup order at a glance

`self.config(key, default)` resolves in this order and returns the first match:

1. Plugin-scoped value from `plugin_configs[plugin_name][key]`
2. Schema default from `config_schema[key].default` (when a schema is declared)
3. `CoreConfig` attribute `getattr(core.config, key, ...)`
4. The `default` argument you passed to `self.config()`

Each source is a fallback for the one above it, so you can override any value without touching the plugin code.

---

To learn how to declare a schema with typed fields and required values, see [Declare plugin configuration](how-to-declare-plugin-configuration.md). For the design rationale behind this lookup order, see the [plugin architecture explanation](../explanation/plugin-architecture.md).
