# Use error handler decorator

`@handle_errors` wraps a plugin method so that any exception is caught, logged,
and reported as a `core.plugin_error` event — without crashing the plugin.

## Add resilient error handling to a plugin method

**Goal:** decorate a plugin method so that failures are caught, logged at the
right severity, and surfaced as observable events.

1. Import `handle_errors` from `uxok.plugin`.

    ```python
    from uxok.plugin import handle_errors, Plugin
    ```

2. Apply `@handle_errors()` to the method. Write the method body as if it will
   always succeed — no `try`/`except` needed.

    ```python
    class StoragePlugin(Plugin):
        @handle_errors()
        async def save(self, record: dict) -> bool:
            await self.db.insert(record)
            return True
    ```

    When an exception occurs, the decorator logs it at `ERROR` level, emits
    `core.plugin_error`, and returns `None`.

3. Set `return_on_error` when callers expect a typed result. `None` is not always
   a safe sentinel — a method returning a list should return `[]` on failure, not
   `None`.

    ```python
    class StoragePlugin(Plugin):
        @handle_errors(return_on_error=[])
        async def fetch_records(self, query: str) -> list:
            return await self.db.search(query)
    ```

    Pass any value that makes sense for the return type: `False` for booleans,
    `{}` for dicts, `0` for counters.

4. Set `log_level` to match the severity of the failure. The default is `"ERROR"`.
   Use `"WARNING"` for transient or retryable failures; use `"INFO"` only for
   intentionally diagnostic paths.

    ```python
    class APIClient(Plugin):
        @handle_errors(log_level="WARNING", return_on_error=None)
        async def fetch_with_fallback(self, url: str):
            return await self.http.get(url, timeout=2)
    ```

5. Set `emit_event=False` to suppress the `core.plugin_error` event when the
   failure is expected and does not need observation. The method still logs and
   returns the configured value.

    ```python
    @handle_errors(emit_event=False, log_level="INFO", return_on_error=None)
    async def probe_optional_service(self) -> None:
        await self.optional.ping()
    ```

## Compose with other decorators

`@handle_errors` stacks with `@hook` and `@event`. Place it directly above the
method so it wraps the fully-decorated callable.

```python
from uxok.plugin import handle_errors, hook, Plugin

class ProcessorPlugin(Plugin):
    @hook("data.transform")
    @handle_errors(emit_event=True, return_on_error=None)
    async def transform(self, data: dict) -> dict | None:
        return await self.pipeline.run(data)
```

## Observe errors from other plugins

Subscribe to `core.plugin_error` from any plugin to react to failures emitted by
`@handle_errors`. The payload always includes `plugin_id`, `plugin_name`, `method`,
`error`, `error_type`, and `source` (`"handled_method"`).

```python
from uxok import event, Plugin

class MonitorPlugin(Plugin):
    @event("core.plugin_error")
    async def on_error(self, evt) -> None:
        name = evt.data["plugin_name"]
        method = evt.data["method"]
        msg = evt.data["error"]
        await self.emit("alert.triggered", {"summary": f"{name}.{method}: {msg}"})
```

!!! note
    `core.plugin_error` is emitted only when `@handle_errors` wraps a `Plugin`
    subclass method. A duck-typed object that has `emit` but does not extend
    `Plugin` receives a `plugin.error` event on itself instead — a legacy
    fallback path.

See [how to subscribe to events](how-to-subscribe-to-events.md) for subscribing
to `core.plugin_error` across the plugin graph, and [how to register hook
handlers](how-to-register-hook-handlers.md) for composing `@hook` with error
handling.
