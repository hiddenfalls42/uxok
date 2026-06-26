# Extend the Plugin base class

## 1. Subclass Plugin

Import `Plugin` from `uxok` and subclass it. All `Plugin` constructor arguments are keyword-only. The kernel attaches itself to the plugin at registration time, so there is no `core` parameter.

```python
from uxok import Plugin

class DataProcessor(Plugin):
    def __init__(self) -> None:
        super().__init__()
```

The `name` parameter is optional. When omitted, the kernel derives it from the class name — `DataProcessor` becomes `"data_processor"`. Pass `name=` explicitly to override.

## 2. Declare metadata

Provide `provides`, `requires`, and related metadata in the same `super().__init__()` call. The kernel validates these at registration time, before your plugin starts.

```python
class DataProcessor(Plugin):
    def __init__(self) -> None:
        super().__init__(
            version="1.0.0",
            description="Processes incoming data streams",
            provides={"processing"},
            requires={"storage"},
            hooks_consumed={"data.transform"},
            events_published={"data.processed"},
            tags={"fast"},
        )
```

`tags` is a set of strings. Other plugins use tags to filter capability providers with `get_capability(tag=...)`. For capability details, see [how to work with capabilities](how-to-work-with-capabilities.md).

## 3. Override on_start and on_stop

Override `on_start()` for initialization logic and `on_stop()` for cleanup. Both are async. The kernel calls them in order: `on_start()` on registration, `on_stop()` on removal or core shutdown.

```python
class DataProcessor(Plugin):
    async def on_start(self) -> None:
        self.storage = await self.get_capability("storage")
        await self.storage.connect()

    async def on_stop(self) -> None:
        await self.storage.disconnect()
```

## 4. Subscribe to events and register hook handlers

Use the `@event` decorator for event subscriptions and `@hook` for hook handlers. The kernel discovers decorated methods at initialization and registers them automatically.

```python
from uxok import Plugin, event, hook
from uxok.protocols import Event

class DataProcessor(Plugin):
    def __init__(self) -> None:
        super().__init__(provides={"processing"})

    @event("data.incoming")
    async def handle_incoming(self, evt: Event) -> None:
        await self.emit("data.processed", {"result": evt.data})

    @hook("data.transform", priority=10)
    async def transform(self, payload: dict) -> dict:
        return {"transformed": True, **payload}
```

`@event` accepts glob patterns such as `"data.*"`. `@hook` accepts a `priority` integer — higher values run first, default is `0`.

To subscribe or register handlers at runtime instead of at class definition, call `await self.subscribe(pattern, handler)` and `await self.register_hook(name, handler, priority=0)` from `on_start()`.

## 5. Emit events

Call `self.emit(event_name, data)` from any method. The event name is published verbatim — no prefix is added. `Event.source` is set to this plugin's name automatically.

```python
async def on_start(self) -> None:
    await self.emit("data.ready", {"plugin": self.metadata.name})
```

To defer emission to a future tick, pass `at_tick=core.tick + N`:

```python
await self.emit("data.scheduled", payload, at_tick=self.core.tick + 5)
```

## 6. Preserve state across hot reloads

Override `get_state()` and `restore_state()` to carry runtime data across a hot-reload swap. The kernel calls `get_state()` on the old instance and `restore_state()` on the new one after it has started.

```python
class DataProcessor(Plugin):
    async def get_state(self) -> dict:
        return {"processed_count": self._processed_count}

    async def restore_state(self, state: dict) -> None:
        self._processed_count = state.get("processed_count", 0)
```

Return only plain, serializable data from `get_state()`. For a full walkthrough of the reload swap, see [how to use hot reload](how-to-use-hot-reload.md).
