# Use capability policies

Capability policies govern three moments: when two plugins claim the same
capability, when multiple providers exist and one must be chosen, and when no
provider exists. Each moment has its own field on `Core`.

## Understand the three policy fields

`Core` accepts three keyword arguments that define capability resolution.

| Field | Controls | Values |
|---|---|---|
| `capability_collision` | What happens when a second plugin registers a capability already provided | `"last_wins_with_warning"` (default), `"error_on_conflict"`, `"first_wins"` |
| `capability_selection` | Which provider is returned when multiple exist | `"last_registered"` (default), `"first_registered"` |
| `capability_missing` | What `get_capability()` does when no provider exists | `"raise"` (default), `"return_none"` |

The defaults allow duplicate registrations and warn, select the newest provider,
and raise `CapabilityError` when nothing is found.

`capability_access` is a separate, fourth axis — security posture, not resolution policy. It controls which plugins may call `get_capability()` for a capability at all, independent of how the kernel selects among providers. See [Lock down capability access](how-to-lock-down-capability-access.md) and the [secure capability access explanation](../explanation/secure-capability-access.md).

## Enforce strict uniqueness in production

1. Create a `Core` with `capability_collision="error_on_conflict"`.

    ```python
    from uxok import Core, PluginError

    core = Core(capability_collision="error_on_conflict")
    ```

2. Register the first provider — this succeeds.

    ```python
    await core.start()
    await core.register_plugin(StoragePluginA())
    ```

3. Attempt to register a second plugin that claims the same capability.

    ```python
    try:
        await core.register_plugin(StoragePluginB())
    except PluginError as exc:
        print(exc)  # "Capability 'storage' not available. Available: StoragePluginA"
    ```

    `PluginError` is raised before `StoragePluginB` is added to the registry; the
    check is atomic — if one capability collides, none from that plugin register.

## Allow override during development

1. Create a `Core` with `capability_collision="last_wins_with_warning"` and
   `capability_selection="last_registered"`. These are the defaults, so explicit
   kwargs are optional but make the intent readable.

    ```python
    core = Core(
        capability_collision="last_wins_with_warning",
        capability_selection="last_registered",
    )
    ```

2. Register two providers for the same capability.

    ```python
    await core.start()
    await core.register_plugin(StoragePluginA())
    await core.register_plugin(StoragePluginB())
    ```

    Both registrations succeed. The second logs a `WARNING` naming both
    plugins.

3. Call `get_capability()` — the last-registered provider is returned.

    ```python
    storage = await core.get_capability("storage")
    # storage is StoragePluginB
    ```

    This replaces the real provider with a stub during development without
    touching the dependent plugin.

## Freeze the first provider silently

1. Create a `Core` with `capability_collision="first_wins"`.

    ```python
    core = Core(capability_collision="first_wins")
    ```

2. Register two providers.

    ```python
    await core.start()
    await core.register_plugin(StoragePluginA())
    await core.register_plugin(StoragePluginB())
    ```

    The second registration is silently ignored. A `DEBUG`-level message is
    logged.

3. Call `get_capability()` — the first-registered provider is returned.

    ```python
    storage = await core.get_capability("storage")
    # storage is StoragePluginA
    ```

    Use this to pre-register a test stub; later registrations silently defer to it.

## Handle optional capabilities gracefully

1. Create a `Core` with `capability_missing="return_none"`.

    ```python
    core = Core(capability_missing="return_none")
    ```

2. Call `get_capability()` for a capability that may not exist.

    ```python
    async def process(self) -> None:
        cache = await self.core.get_capability("cache")
        if cache is not None:
            return await cache.get("key")
        return self.compute_slowly()
    ```

    With the default `"raise"` policy, the call raises `CapabilityError` naming
    every registered capability. Use `"raise"` when absence indicates a
    configuration mistake; use `"return_none"` when the capability is optional
    and a fallback exists.

## Combine policies

The three fields are independent. Mix them to match each deployment's needs.

```python
core = Core(
    capability_collision="error_on_conflict",  # fail fast on duplicates
    capability_selection="first_registered",   # deterministic when multiple exist
    capability_missing="raise",                # no silent degradation
)
```

`Core` validates all three fields in `CoreConfig.__post_init__()`, raising
`ValueError` for unrecognised values before any plugin is registered.

## Related pages

- [Work with capabilities](how-to-work-with-capabilities.md) — declare `provides` and `requires`, and call `get_capability()`
- [Use tags for provider selection](how-to-use-tags-for-provider-selection.md) — narrow a pool of providers at lookup time without changing global policy
