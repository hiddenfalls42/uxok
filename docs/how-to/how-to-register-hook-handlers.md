# Register hook handlers

Hooks are synchronous extension points: when a caller executes a hook by name, all registered handlers run in priority order inside the caller's task and return their results. This guide covers every way to register a handler and control its behavior.

## Register a handler with the decorator

1. Import `hook` from `uxok`.

    ```python
    from uxok import Core, Plugin, hook
    ```

2. Decorate a method on your `Plugin` subclass with `@hook(name)`.

    ```python
    class DataPlugin(Plugin):
        @hook("data.process")
        async def process(self, data: dict) -> dict:
            return {"processed": True, **data}
    ```

    The framework discovers `process` at instantiation time and registers it with the hook system when the plugin starts. You do not call `register_hook` yourself.

3. Register the plugin with the core as usual.

    ```python
    core = Core()
    plugin = DataPlugin()
    await core.register_plugin(plugin)
    ```

Hook names are global. No prefix is added. Use dot-separated namespaces like `"data.process"` or `"user.validate"` to avoid collisions across plugins.

## Set execution priority

1. Pass `priority` to `@hook`. Higher values run first; the default is `0`.

    ```python
    class ValidationPlugin(Plugin):
        @hook("data.validate", priority=100)
        async def check_schema(self, data: dict) -> bool:
            return "id" in data

        @hook("data.validate", priority=50)
        async def check_business_rules(self, data: dict) -> bool:
            return data.get("status") in {"active", "pending"}

        @hook("data.validate", priority=10)
        async def check_permissions(self, data: dict) -> bool:
            return data.get("role") != "guest"
    ```

    With these registrations, `check_schema` runs first, then `check_business_rules`, then `check_permissions`.

2. Use negative priority for fallback handlers — handlers that should run only when nothing else has handled the hook.

    ```python
    class DefaultsPlugin(Plugin):
        @hook("cache.get", priority=-100)
        async def fallback_cache_miss(self, key: str) -> None:
            return None  # Nothing in cache
    ```

## Use firstresult mode

1. Call the hook with `firstresult=True` from inside a plugin to stop execution at the first handler that returns a non-`None` value.

    ```python
    result = await self.hook("cache.get", key, firstresult=True)
    ```

    The remaining handlers are skipped. This is the standard pattern for pluggable lookup chains where one provider is sufficient.

2. A handler that wants to be skipped returns `None` explicitly.

    ```python
    class CachePlugin(Plugin):
        @hook("cache.get", priority=50)
        async def memory_cache(self, key: str) -> str | None:
            return self._memory.get(key)  # Returns None on miss → next handler runs

        @hook("cache.get", priority=10)
        async def disk_cache(self, key: str) -> str | None:
            return self._disk.get(key)
    ```

## Implement conditional logic inside the handler

`@hook` accepts only `name` and `priority`. Conditional behavior belongs inside the handler body.

```python
class ProcessingPlugin(Plugin):
    @hook("data.process")
    async def process_active(self, data: dict) -> dict | None:
        if not data.get("active", True):
            return None  # Skip: signal no result from this handler
        return {"processed": True, **data}
```

Returning `None` from a handler always means "no result from this handler." Callers collecting all results receive `None` in the list at that position; callers using `firstresult=True` skip it and continue to the next handler.

## Register a handler dynamically

1. Define the handler as a regular method (no decorator).

2. Call `self.register_hook` inside `on_start`.

    ```python
    class DynamicPlugin(Plugin):
        async def on_start(self) -> None:
            await self.register_hook("data.process", self._my_handler, priority=5)

        async def _my_handler(self, data: dict) -> dict:
            return {"dynamic": True, **data}
    ```

    `register_hook` is the method `@hook` desugars to. Both paths bind the handler to this plugin instance: the kernel drains all of them automatically when the plugin stops or is reloaded.

3. Register closures the same way when you need to capture local state.

    ```python
    class ContextPlugin(Plugin):
        async def on_start(self) -> None:
            threshold = self.config("threshold", default=0.5)

            async def scored_handler(value: float) -> float | None:
                return value if value >= threshold else None

            await self.register_hook("score.evaluate", scored_handler, priority=20)
    ```

## Declare hooks consumed in metadata

Handlers registered on a hook name are the consuming side of a contract. Declare which hook names your plugin consumes so introspection tools and human readers know the dependency.

```python
class ConsumerPlugin(Plugin):
    def __init__(self) -> None:
        super().__init__(
            hooks_consumed={"data.process", "data.validate"},
        )

    @hook("data.process")
    async def handle_process(self, data: dict) -> dict:
        return {**data, "handled": True}
```

`hooks_consumed` is metadata only — it does not affect registration or execution.

See the [hook system explanation](../explanation/hook-system.md) for the execution model, priority semantics, and the firstresult contract in depth.
