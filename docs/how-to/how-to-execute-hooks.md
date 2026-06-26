# Execute hooks

Hooks are named extension points that collect return values from all registered
handlers. Call them with `self.hook()` from any method in your plugin; handlers
run priority-sorted and serially in the caller's task.

## Call a hook and collect results

1. Call `await self.hook(name, *args, **kwargs)`. It returns a list of results,
   one per registered handler (including `None` for handlers that returned nothing
   or failed).

```python
class DataProcessor(Plugin):
    async def validate_data(self, data: dict) -> list:
        results = await self.hook("data.validate", data)
        return [r for r in results if r is not None]
```

## Return the first successful result

1. Pass `firstresult=True`. Execution stops at the first handler that returns a
   non-`None` value; that value is returned directly instead of a list.

```python
class CachePlugin(Plugin):
    async def get_cached_value(self, key: str) -> object:
        return await self.hook("cache.get", key, firstresult=True)
```

Use `firstresult=True` when multiple handlers implement the same capability and
you want the first successful response — for example, a chain of cache layers.

## Pass keyword arguments to handlers

1. Pass any keyword arguments after the hook name. Every handler in the chain
   receives them.

```python
class APIPlugin(Plugin):
    async def call_api(self, endpoint: str, timeout: int = 30) -> list:
        return await self.hook("api.call", endpoint, timeout=timeout)
```

The corresponding handler declares the same parameters:

```python
@hook("api.call")
async def handle_api_call(self, endpoint: str, timeout: int = 30) -> dict:
    return await make_request(endpoint, timeout=timeout)
```

## Chain hooks into a pipeline

1. Call `await self.hook(...)` sequentially, feeding each result into the next call.
   Each call returns before the next begins.

```python
class PipelinePlugin(Plugin):
    async def run_pipeline(self, raw_data: dict) -> list | None:
        validated = await self.hook("data.validate", raw_data, firstresult=True)
        if validated is None:
            return None

        transformed = await self.hook("data.transform", validated, firstresult=True)
        enriched = await self.hook("data.enrich", transformed or validated)
        return enriched
```

Conditional logic between calls belongs in the caller, not in the hook system.

## Defer a hook to a future tick

1. Pass `at_tick=` with a tick number greater than `self.core.tick`. The call
   schedules the hook and returns `None` immediately — do not `await` the return value.

```python
class ScheduledPlugin(Plugin):
    async def schedule_processing(self) -> None:
        target_tick = self.core.tick + 200
        self.hook("data.process", {"batch": "nightly"}, at_tick=target_tick)
```

`ValueError` is raised immediately if `at_tick` is less than or equal to the
current tick.

## Schedule a recurring hook

There is no repeat parameter. Schedule the next execution from inside the handler.

1. In `on_start`, fire the first execution.
2. At the end of the handler, schedule the next one.

```python
INTERVAL = 100

class PeriodicPlugin(Plugin):
    async def on_start(self) -> None:
        self.hook("data.process", {"batch": "daily"}, at_tick=self.core.tick + INTERVAL)

    @hook("data.process")
    async def process(self, data: dict) -> None:
        # work here
        self.hook("data.process", data, at_tick=self.core.tick + INTERVAL)
```

## Handle errors from hook chains

A handler that raises an exception returns `None` for that slot and publishes a
`core.hook_error` event. The rest of the chain continues.

1. Filter `None` values to distinguish results from silent failures.
2. Subscribe to `core.hook_error` to observe which handler failed.

```python
class AggregatorPlugin(Plugin):
    async def collect_metrics(self) -> dict:
        results = await self.hook("metrics.collect")
        valid = [r for r in results if r is not None]

        metrics: dict = {}
        for result in valid:
            metrics.update(result)
        return metrics
```

## Declare which hooks your plugin consumes

1. Pass `hooks_consumed=` in `super().__init__()` to document the hook dependency
   in the plugin's metadata.

```python
class DataProcessor(Plugin):
    def __init__(self) -> None:
        super().__init__(
            hooks_consumed={"data.validate", "data.transform"},
        )
```

This metadata is visible through `core.list()` introspection and helps
consumers understand the plugin's coordination surface.

## Register a handler dynamically

The `@hook` decorator runs at class-definition time and registers on `start()`.
To register a handler after start — for example, a closure — call
`await self.register_hook(name, handler, priority=N)` directly. The handler is
cleaned up automatically on stop or hot reload.

```python
class DynamicPlugin(Plugin):
    async def on_start(self) -> None:
        threshold = self.config("threshold", default=0.9)

        async def check(value: float) -> bool:
            return value >= threshold

        await self.register_hook("quality.check", check, priority=5)
```

See the [hook system explanation](../explanation/hook-system.md) for how priority
ordering, serial execution, and the atomic-frame property work together.
