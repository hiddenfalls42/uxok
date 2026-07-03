# How-to guides

Each guide solves one task with numbered steps and working code. They assume you already know the basics — if you're starting from scratch, the [tutorial](../tutorials/index.md) comes first.

## Capabilities

Start here — the kernel's core wiring mechanism, where plugins declare what they provide and require.

- [Work with capabilities](how-to-work-with-capabilities.md) — Declare `provides`/`requires`, resolve providers with `get_capability()`, tag providers, and handle missing capabilities.
- [Use tags for provider selection](how-to-use-tags-for-provider-selection.md) — Pick one provider by `tag` when several plugins offer the same capability — swap a backend without touching policy.
- [Use capability policies](how-to-use-capability-policies.md) — Configure `capability_collision`, `capability_selection`, and `capability_missing` on `Core` for different deployment environments.
- [Lock down capability access](how-to-lock-down-capability-access.md) — Set `capability_access` to restrict each plugin to its declared grant; handle `CapabilityAccessError` and `StalePluginError`.
- [Resolve a live plugin instance](how-to-resolve-a-live-plugin-instance.md) — Use the `kernel.lifecycle` grant to act on a discovered plugin instance directly.
- [Probe a plugin before admission](how-to-probe-a-plugin-before-admission.md) — Call `check_plugin()` to validate a candidate against the live graph before committing to `register_plugin()`.

## Core lifecycle

- [Manage core lifecycle](how-to-manage-core-lifecycle.md) — Start, stop, restart, and observe state transitions through `INITIALIZED → RUNNING → STOPPING → STOPPED`.
- [Boot a plugin graph in order](how-to-boot-a-plugin-graph-in-order.md) — Register providers before consumers, factor the sequence into `build_host()`, and poll for capabilities that arrive asynchronously.
- [Shut down gracefully](how-to-shut-down-gracefully.md) — Build a `ShutdownHandler` that traps OS signals and the `system.shutdown` event, and blocks `main()` until shutdown is requested.
- [Use hot reload](how-to-use-hot-reload.md) — Load or swap plugins at runtime without restarting the core, using `core.load_plugin()`.

## Plugin development

- [Extend the Plugin base class](how-to-extend-plugin-base.md) — Subclass `Plugin`, declare metadata (`provides`, `requires`, `tags`), and implement `on_start()` / `on_stop()`.
- [Declare plugin configuration](how-to-declare-plugin-configuration.md) — Define a `config_schema` with `ConfigField` and `REQUIRED` for type-safe, validated configuration.
- [Use plugin configuration](how-to-use-plugin-configuration.md) — Read configuration values with `self.config()` and supply them via `plugin_configs` on `Core`.

## Events

- [Publish events](how-to-publish-events.md) — Emit events with `plugin.emit()`, inspect `event.source`, and schedule delivery to a future tick with `at_tick=`.
- [Subscribe to events](how-to-subscribe-to-events.md) — Register handlers with `@event()` using exact names or glob patterns.

## Hooks

- [Register hook handlers](how-to-register-hook-handlers.md) — Use `@hook()` to provide extension points with a name and optional priority.
- [Execute hooks](how-to-execute-hooks.md) — Call `plugin.hook()` to invoke handlers in priority order, collect results, or defer to a future tick.

## Plugin management

- [Register and manage plugins](how-to-register-and-manage-plugins.md) — Register, unregister, and query plugins by ID, name, or capability using `core.register_plugin()` and `core.list()`.
- [Manage plugin dependencies](how-to-manage-plugin-dependencies.md) — Declare hard plugin-to-plugin dependencies by UUID, validate load order, and detect circular dependency errors.
- [Use plugin collections](how-to-use-plugin-collections.md) — Query the live plugin graph by capability, hook, or event using `PluginCollection` and chained filters.

## Scheduling

- [Use tick-based scheduling](how-to-use-tick-based-scheduling.md) — Schedule events and hooks at precise tick boundaries with `emit(at_tick=)` and `hook(at_tick=)`.
