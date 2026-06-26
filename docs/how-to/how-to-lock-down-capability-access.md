# Lock down capability access

By default every plugin can resolve every capability. Setting `capability_access` on `Core` restricts each plugin to only the capabilities it explicitly declares in its manifest.

## 1. Set the access posture on Core

Pass `capability_access` as a keyword argument to `Core`. Three values are accepted: `"open"` (default, no restriction), `"declared"` (manifest-bounded access, raw providers returned), and `"sealed"` (manifest-bounded access, typed resolutions return an attenuating facet).

```python
from uxok import Core

core = Core(capability_access="sealed")
```

`Core` validates the value at construction; an unrecognised string raises `ValueError` before any plugin is registered.

## 2. Register a provider plugin

Declare the capability in `provides` as normal. The posture setting does not change how providers register.

```python
from uxok import Plugin

class GreetingProvider(Plugin):
    def __init__(self):
        super().__init__(name="greeting_provider", provides={"greeting"})

    async def greet(self, name: str) -> str:
        return f"Hello, {name}!"
```

## 3. Declare the consumer's runtime grant

Under `"declared"` and `"sealed"`, a plugin may only resolve capabilities in its runtime grant — the union of `requires` and `resolves`.

Use **`requires`** when the provider must be running before your plugin starts. The kernel validates `requires` at registration: if no live provider covers the name, registration fails with `MissingCapabilityError`. The grant is also the runtime allow-list.

Use **`resolves`** when you need runtime access without a load-order constraint — for lazy resolutions or capabilities that may not exist at registration time. Names in `resolves` are never checked at registration.

```python
class Consumer(Plugin):
    def __init__(self):
        super().__init__(
            name="consumer",
            requires={"greeting"},   # load-order check + runtime grant
        )

    async def on_start(self) -> None:
        greeter = await self.get_capability("greeting")
        print(await greeter.greet("world"))  # succeeds — "greeting" is in grant
```

## 4. Catch CapabilityAccessError for out-of-grant resolutions

A plugin that calls `get_capability()` for a name outside its `requires | resolves` union receives `CapabilityAccessError` immediately — the registry is never consulted.

```python
from uxok import CapabilityAccessError

class Overreacher(Plugin):
    def __init__(self):
        super().__init__(name="overreacher")  # no grant declared

    async def on_start(self) -> None:
        try:
            await self.get_capability("greeting")
        except CapabilityAccessError as exc:
            print(f"Blocked: '{exc.capability}' not in grant")
```

## 5. Handle StalePluginError in sealed mode (typed resolutions)

Under `"sealed"`, a typed resolution — `await self.get_capability(SomeProtocol)` — returns a live-resolving facet rather than the raw provider. When the provider is revoked while you hold the facet, the next method call raises `StalePluginError`.

```python
from uxok import StalePluginError

class HealthCheck(Plugin):
    async def ping(self) -> None:
        try:
            result = await self.greeter.greet("world")
        except StalePluginError:
            # Provider was unregistered; re-resolve or degrade gracefully
            self.greeter = await self.get_capability("greeting")
```

## Runnable example

```python
import asyncio
from uxok import Core, Plugin, CapabilityAccessError

class GreetingProvider(Plugin):
    def __init__(self):
        super().__init__(name="greeting_provider", provides={"greeting"})

    async def greet(self, name: str) -> str:
        return f"Hello, {name}!"

class Consumer(Plugin):
    def __init__(self):
        super().__init__(name="consumer", requires={"greeting"})

    async def on_start(self) -> None:
        greeter = await self.get_capability("greeting")
        print(await greeter.greet("world"))  # prints: Hello, world!

class Overreacher(Plugin):
    def __init__(self):
        super().__init__(name="overreacher")

    async def on_start(self) -> None:
        try:
            await self.get_capability("greeting")
        except CapabilityAccessError as exc:
            print(f"Blocked: {exc.capability}")  # prints: Blocked: greeting

async def main() -> None:
    core = Core(capability_access="sealed")
    await core.start()
    await core.register_plugin(GreetingProvider())
    await core.register_plugin(Consumer())
    await core.register_plugin(Overreacher())
    await core.stop()

asyncio.run(main())
```

## Related pages

- [Secure capability access](../explanation/secure-capability-access.md) — The full access model: posture modes, self.core attenuation, the sealed facet, and the return guard
- [Work with capabilities](how-to-work-with-capabilities.md) — Declare `provides` and `requires`, and call `get_capability()`
- [Use capability policies](how-to-use-capability-policies.md) — Configure the three resolution axes independently of the access posture
