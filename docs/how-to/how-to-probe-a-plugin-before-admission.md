# Probe a plugin before admission

`Core.check_plugin()` answers whether a candidate plugin fits the live graph without registering it. Call it before committing to `register_plugin()` to discover missing requirements, ID conflicts, and capability collisions in advance.

## 1. Build the candidate

Instantiate the plugin without registering it. Construction is safe to call at any time; the core is not involved until `register_plugin()`.

```python
from uxok import Plugin

class DataConsumer(Plugin):
    def __init__(self):
        super().__init__(name="data_consumer", requires={"storage"})
```

```python
candidate = DataConsumer()
```

## 2. Call check_plugin

Pass the candidate to `core.check_plugin()`. The probe reads the live graph and returns an `AdmissionResult`. It mutates nothing — no registration, no `on_start()`, no hooks, no events.

```python
result = await core.check_plugin(candidate)
```

The probe is also available from inside a running plugin via `self.core.check_plugin()`, without any capability grant.

## 3. Branch on the result

`AdmissionResult.ok` is `True` when the candidate has no admission faults. Use it as the branch condition.

```python
if result.ok:
    await core.register_plugin(candidate)
else:
    print("Candidate would not be admitted")
```

## 4. Read per-fault detail

When `ok` is `False`, four fields describe what went wrong. Any combination may be set.

| Field | Type | Meaning |
|---|---|---|
| `missing_requires` | `frozenset[str]` | `requires` names with no live provider |
| `id_conflict` | `bool` | The candidate's ID is already registered |
| `provides_conflicts` | `frozenset[str]` | Capabilities that collide under `error_on_conflict` |
| `contract_failures` | `frozenset[str]` | Typed capabilities whose provider fails the protocol check |

```python
if result.missing_requires:
    print(f"Missing providers: {sorted(result.missing_requires)}")

if result.id_conflict:
    print("ID already registered — use load_plugin() to reload")

if result.provides_conflicts:
    print(f"Capability conflicts: {sorted(result.provides_conflicts)}")
```

!!! note
    The probe is advisory. It holds no lifecycle lock, so a concurrent registration can change the graph between the probe and the commit. `register_plugin()` runs the same admission check atomically under the lock — a clean probe does not guarantee commit success.

## Runnable example

```python
import asyncio
from uxok import Core, Plugin

class StorageProvider(Plugin):
    def __init__(self):
        super().__init__(name="storage_provider", provides={"storage"})

class DataConsumer(Plugin):
    def __init__(self):
        super().__init__(name="data_consumer", requires={"storage"})

class AnalyticsConsumer(Plugin):
    def __init__(self):
        super().__init__(name="analytics_consumer", requires={"analytics"})

async def main() -> None:
    core = Core()
    await core.start()
    await core.register_plugin(StorageProvider())

    # Candidate whose requires are satisfied
    good = DataConsumer()
    result = await core.check_plugin(good)
    print(result.ok)              # True
    print(result.missing_requires)  # frozenset()

    # Candidate with a missing provider
    bad = AnalyticsConsumer()
    result = await core.check_plugin(bad)
    print(result.ok)              # False
    print(result.missing_requires)  # frozenset({'analytics'})

    await core.stop()

asyncio.run(main())
```

## Related pages

- [Secure capability access](../explanation/secure-capability-access.md) — How check_plugin fits into the declared/sealed posture model
- [Register and manage plugins](how-to-register-and-manage-plugins.md) — Full registration lifecycle and dependency management
- [Capability system](../explanation/capability-system.md) — How requires and provides interact at registration time
