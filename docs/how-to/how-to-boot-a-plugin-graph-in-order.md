# Boot a plugin graph in order

A real host registers many plugins, and they depend on each other. `core.load_plugins()` takes a batch of plugin sources, works out a valid load order from their declared `provides`/`requires`, and commits the whole graph in one atomic step — reach for it first. Its best-effort sibling `core.try_load_plugins()` boots whatever resolves and reports the rest instead of refusing the batch — the right choice when the sources are independently authored and one bad file must not empty the boot. This guide leads with those two primitives, then covers the lower-level manual-ordering and capability-polling patterns for what they do not cover: registering already-live instances, or waiting on a capability that shows up asynchronously after boot.

## 1. Boot the whole graph with `load_plugins`

`core.load_plugins()` accepts an iterable of `(code, origin)` sources — the same shape as `load_plugin`'s two positional arguments, one tuple per file. It materializes every source, computes a topological order from the candidates' declared `provides`/`requires` (plus whatever is already live), and commits them under a single hold of the lifecycle lock. It returns the plugin names in commit order:

```python
plugin_paths = sorted(p for p in plugin_dir.glob("*.py") if p.name != "host.py")
sources = [(p.read_text(), str(p)) for p in plugin_paths]
names = await core.load_plugins(sources)
```

Discovery — which folder, which files, which exclusions — stays the host's job; `load_plugins` only orders and commits the sources you hand it. It is fresh-load-only: a candidate whose name matches an already-live plugin is rejected before anything commits (use `load_plugin` for hot-reload).

A failure raises `BatchLoadError` with an `installed` tuple — everything that committed before the failure, in commit order — so the host decides its own rollback policy:

```python
# Keep the whole graph or nothing:
try:
    names = await core.load_plugins(sources)
except BatchLoadError as e:
    for name in reversed(e.installed):      # () on a plan-phase fault → no-op
        await core.unregister_plugin(name)
    raise

# Boot whatever resolves, keep the prefix:
try:
    names = await core.load_plugins(sources)
except BatchLoadError as e:
    if e.phase == "plan":
        raise                                # graph malformed; nothing came up
    names = e.installed                      # partial boot, already live
```

`e.phase` is `"plan"` for any fault caught before anything commits — a dependency cycle, a missing capability, a duplicate name, a source that fails to materialize, a `max_plugins` overflow, a provider that does not implement its declared protocol, or (under `error_on_conflict`) a duplicate provider — and `"commit"` when a candidate's own `on_start()` raised partway through the batch. `e.failed` names the offending candidate — its origin, its plugin name, or a `"sources[N]"` positional sentinel for an unnamed source that failed to materialize — and is `None` only for faults not attributable to one candidate (a cycle, a duplicate-provider collision, a `max_plugins` overflow); `e.cause` is the underlying exception. See [`API.md`](../manifests/API.md) for the full contract.

## 2. Boot the loadable subset with `try_load_plugins`

When the sources come from a folder of independently authored files, one broken or conflicting file should not abort the whole boot. `core.try_load_plugins()` takes the same `(code, origin)` iterable, commits the **maximal loadable subgraph**, and returns a `BatchLoadReport` instead of ever raising `BatchLoadError`:

```python
report = await core.try_load_plugins(sources)
for name, origin in report.loaded:            # committed, in topological order
    log.info("loaded %s from %s", name, origin)
for skip in report.skipped:                   # excluded, in input order
    log.warning("skipped %s (%s): %s", skip.origin, skip.reason, skip.cause)
```

`report.loaded` and `report.skipped` partition the input exactly — every source lands in one or the other. Each `SkippedSource` carries the `origin` as you supplied it, the plugin `name` (or `None` if it never materialized), a `reason` from a closed vocabulary, and the underlying `cause`. The reasons let a host branch on *why* a file dropped out: `materialize_error` (it failed to compile, its module body raised, or its `__init__` raised), `duplicate_name`, `live_name_collision`, `missing_capability`, `cycle_member`, `contract_failure`, `duplicate_provider`, `max_plugins`, `dependent_of_skipped` (a transitive victim — its only provider was itself skipped), or `on_start_error` (admission passed but its `on_start()` raised at commit).

Pruning is transitive: if a plugin is skipped, anything that required a capability only it provided is skipped too, as `dependent_of_skipped`. Everything independent still boots. `try_load_plugins` never unregisters an already-live plugin and never unwinds a committed one, so a host can resubmit the skipped sources later — after the missing provider hot-loads, say — without disturbing the running graph. A `dependent_of_skipped` or `missing_capability` file is worth retrying; a `materialize_error` one is broken source that needs fixing first:

```python
report = await core.try_load_plugins(sources)
# ...later, once more plugins are available — resubmit the still-broken ones,
# reusing the original (code, origin) tuples rather than reconstructing them:
retryable = {s.origin for s in report.skipped if s.reason != "materialize_error"}
retry = [(code, origin) for code, origin in sources if origin in retryable]
report = await core.try_load_plugins(retry)
```

Choose between the two verbs by policy: `load_plugins` when the graph is curated and interdependent (all or nothing); `try_load_plugins` when partial success is useful and each file stands on its own. See [`API.md`](../manifests/API.md) §7.7–§7.8 for the full report shape.

## 3. Register providers before consumers (manual ordering)

Reach for manual `register_plugin()` calls when you already hold live `Plugin` instances rather than source strings, or need to interleave registration with other host logic `load_plugins` doesn't model. The same ordering rule applies: `core.register_plugin()` raises `MissingCapabilityError` if a plugin's `requires` set names a capability no registered plugin provides yet. Register along the dependency arrows — providers first, consumers last.

```python
import asyncio
from uxok import Core, Plugin


class Storage(Plugin):
    def __init__(self):
        super().__init__(name="storage", provides={"storage"})


class Indexer(Plugin):
    def __init__(self):
        super().__init__(name="indexer", requires={"storage"})

    async def on_start(self):
        self.storage = await self.get_capability("storage")


async def main():
    core = Core()
    await core.start()
    await core.register_plugin(Storage())   # provider first
    await core.register_plugin(Indexer())   # consumer second
    await core.stop()


asyncio.run(main())
```

Reversing the two `register_plugin` calls raises `MissingCapabilityError` on the `Indexer`, because `"storage"` is not yet in the graph.

## 4. Factor the boot sequence into one function

Keep the registration sequence in a single `build_host()` function rather than scattering it through `main()`. The running program and the test suite can then boot the identical graph, so they never drift.

```python
async def build_host(core: Core) -> None:
    await core.register_plugin(Storage())
    await core.register_plugin(Indexer())
```

## 5. Poll for a capability that arrives later

Not every capability is present at boot. A plugin may be hot-loaded after startup (see [Use hot reload](how-to-use-hot-reload.md)), or a provider may register before its own async setup makes it usable. For these, poll at the host level until the capability resolves, then register the consumer that requires it.

```python
async def wait_for_capability(core: Core, name: str, timeout: float = 5.0) -> bool:
    """Poll until `name` is resolvable, or give up after `timeout` seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            await core.get_capability(name)
            return True
        except Exception:
            await asyncio.sleep(0.05)
    return False
```

Use it to gate a later registration step on a capability that some earlier plugin loads in the background:

```python
if await wait_for_capability(core, "metrics", timeout=2.0):
    await core.register_plugin(Dashboard())   # requires {"metrics"}
else:
    raise RuntimeError("metrics provider never came up")
```

The poll loop catches the lookup exception and retries; once a provider registers `"metrics"`, the next `get_capability()` succeeds and the function returns `True`. The bounded timeout turns a missing dependency into an explicit failure instead of a hang.

See [Work with capabilities](how-to-work-with-capabilities.md) for the `requires`/`resolves` distinction, [Manage plugin dependencies](how-to-manage-plugin-dependencies.md) for hard plugin-to-plugin (UUID) dependencies, and [Shut down gracefully](how-to-shut-down-gracefully.md) for the other end of the host lifecycle.
