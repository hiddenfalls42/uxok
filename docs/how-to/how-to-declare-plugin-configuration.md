# Declare plugin configuration

`ConfigField` lets you declare a typed configuration schema directly on your plugin. The framework validates every field before `on_start()` runs, so your initialization code never receives a wrong type or a missing required value.

## 1. Import the building blocks

```python
from uxok import Plugin, ConfigField, REQUIRED
```

`ConfigField` describes one configuration entry: its expected type, an optional default, and an optional description used in error messages. `REQUIRED` is a sentinel that marks fields that must be explicitly supplied by the caller.

## 2. Declare a schema in `__init__`

Pass a `config_schema` dictionary to `super().__init__()`. Each key is the field name; each value is a `ConfigField`:

```python
class StoragePlugin(Plugin):
    def __init__(self):
        super().__init__(
            name="storage",
            provides={"database"},
            config_schema={
                "db_url":    ConfigField(str,   REQUIRED,  "Database connection URL"),
                "pool_size": ConfigField(int,   default=10, description="Connection pool size"),
                "timeout":   ConfigField(float, default=30.0),
            },
        )
```

`ConfigField` takes three positional-or-keyword parameters:

- `type` — the Python type the value must satisfy (`isinstance` check)
- `default` — a fallback value, or `REQUIRED` if the caller must supply it
- `description` — human-readable text included in validation error messages (optional)

## 3. Mark required fields with `REQUIRED`

Fields set to `REQUIRED` have no fallback. If the caller omits them, validation raises `PluginError` with a message that includes the field's description string:

```python
config_schema={
    "api_key":  ConfigField(str, REQUIRED, "External API authentication key"),
    "endpoint": ConfigField(str, REQUIRED, "API endpoint URL"),
}
```

## 4. Supply values when constructing `Core`

Pass a `plugin_configs` dictionary to `Core`. The top-level key is the plugin name; the inner dictionary contains field-name/value pairs:

```python
from uxok import Core

core = Core(
    plugin_configs={
        "storage": {
            "db_url":    "postgres://localhost/mydb",
            "pool_size": 20,
        }
    }
)
```

Fields absent from `plugin_configs` fall back to their schema default. Fields present in `plugin_configs` must match the declared type.

## 5. Read values with `self.config()`

Call `self.config(key)` anywhere in the plugin — including inside `on_start()` — to retrieve a validated value:

```python
async def on_start(self):
    db_url    = self.config("db_url")
    pool_size = self.config("pool_size")

    self.db   = connect(db_url)
    self.pool = ConnectionPool(pool_size)
```

`self.config()` resolves in this order:

1. Plugin-scoped value from `plugin_configs[plugin_name]`
2. Schema default
3. `CoreConfig` attribute of the same name
4. The `default` argument passed directly to `self.config(key, default=...)`

Because validation runs at `start()`, before `on_start()` is called, the values are already guaranteed to match their declared types by the time you read them.

## 6. Handle validation errors

Validation fires inside `plugin.start()`, which `core.register_plugin()` calls. Catch `PluginError` to surface a clear message before the core finishes starting:

```python
from uxok import Core, PluginError

try:
    await core.register_plugin(StoragePlugin())
except PluginError as e:
    print(f"Configuration error: {e}")
```

The error message names every failing field:

```text
PluginError: Plugin 'storage' config validation failed:
  'db_url' is required but not supplied (Database connection URL)
  'pool_size': expected int, got str
```

Fix the `plugin_configs` dictionary and retry registration.
