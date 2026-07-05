# Locking it down

Starting from the `chat/` package [Watching the graph](watching-the-graph.md) left you
with, you will seal capability access, type the `llm` capability as a `Protocol`, add
the `BatchLoadError` rollback recipe to `host.py`, and add graceful shutdown. This is
the last stage — what you finish with matches
[`examples/example_host/`](https://github.com/hiddenfalls42/uxok/tree/main/examples/example_host)
byte-for-byte.

## What you will change

- `shutdown.py` *(new)* — traps SIGINT/SIGTERM (and a `system.shutdown` event any plugin
  can emit) and blocks `main()` until one arrives
- `model.py` / `terse_model.py` — `provides={"llm"}` becomes `provides={LLM}`, a
  `Protocol` type
- `agent.py` — `requires={"llm"}` becomes `requires={LLM}`; also gains the "agent: ..."
  print that used to live in `host.py`
- `conductor.py` — absorbs the say/reply loop, the roster query, and the closing
  narration that `host.py` used to drive directly
- `host.py` — shrinks to an agnostic loader: boot the folder, roll back on a bad batch,
  resolve `shutdown_handling`, wait
- `flaky.py` *(deleted)* — it only existed to give tutorial 6's supervisor something to
  evict; the final graph needs nothing broken to justify `supervisor.py`'s presence

Running it prints the same conversation and persona swap as tutorial 6, minus the
flaky-eviction sequence, then blocks until Ctrl-C — which now exits cleanly instead of
raising a bare `KeyboardInterrupt`.

## Why the driving logic belongs in a plugin, not the host

Every tutorial from 2 onward has quietly carried a tension: `host.py` decides *when* to
say each line, correlates replies by `cid`, and prints the transcript — all "the host
never mentions plugin behavior" work happening outside any plugin. Tutorial 4 named
this honestly as temporary scaffolding, because at that point in the series no plugin
was free to own it yet.

But `watcher.py` already proved the alternative works: graph control — hot-loading a
changed file through `kernel.lifecycle` — lives in plugin-land, resolved through
`get_capability` like anything else. The demo's say/reply loop is no different in kind.
It's built from `emit`/subscribe/`create_background_task`, exactly like `agent.py`'s own
reply-handling — which means it belongs in a plugin for the same reason `agent.py` is a
plugin and not inlined into `host.py`.

`conductor.py` — small since tutorial 2, just a persona-swap trigger — absorbs the rest:
the cid-correlated `_say`/`_on_reply` loop, the `roster.report` query, and the closing
line. `host.py` no longer prints a single line about what any plugin does; its only
plugin-shaped knowledge is `host_configs()`, which is deployment *data*, not driving
code.

## `conductor.py`: the whole script, in one plugin

```python
class Conductor(Plugin):
    def __init__(self) -> None:
        super().__init__(
            name="conductor",
            requires={"kernel.lifecycle"},
            events_published={"user.says"},
            hooks_consumed={"roster.report"},
            config_schema={
                "autorun": ConfigField(bool, True, "run the scripted demo conversation on start"),
            },
        )
        self._cids = itertools.count(1)
        self._pending: dict[str, asyncio.Future[str]] = {}

    async def on_start(self) -> None:
        self._lifecycle = await self.get_capability("kernel.lifecycle")
        if self.config("autorun"):
            await self.create_background_task(self.run_script(), name="conductor-script")

    @event("agent.says.*")
    async def _on_reply(self, ev: Event) -> None:
        future = self._pending.pop(ev.name.rsplit(".", 1)[-1], None)
        if future is not None and not future.done():
            future.set_result(ev.data["text"])

    async def _say(self, text: str) -> str:
        cid = f"c{next(self._cids)}"
        self._pending[cid] = asyncio.get_running_loop().create_future()
        print(f"user:  {text}")  # noqa: T201
        await self.emit("user.says", {"cid": cid, "text": text})
        return await asyncio.wait_for(self._pending[cid], timeout=2.0)

    async def run_script(self) -> None:
        await self._say("hello there")
        try:
            await self._lifecycle.load_plugin(_GRUMPY.read_text(), origin=str(_GRUMPY))
        except (PluginError, CoreError) as exc:
            logger.warning("conductor: persona hot-swap failed: %s", exc)
            return
        print("...[hot-reloaded the persona]...")  # noqa: T201
        await self._say("what's the weather like?")
        report = await self.hook("roster.report", firstresult=True)
        print(f"roster: {report}")  # noqa: T201
        print("conversation done — Ctrl-C to exit (edit grumpy_persona.py meanwhile!)")  # noqa: T201
```

Everything here is a straight relocation of primitives you've already used — `emit`,
`self.hook(..., firstresult=True)`, `create_background_task` — pointed at a new
purpose. The one new detail is *why* `run_script` runs as a background task instead of
an inline `await` in `on_start`:

`conductor` requires only `"kernel.lifecycle"`, which is always satisfiable, so it
commits in its plain alphabetical slot — before `agent.py`, which is pushed to the very
end of the batch by its `requires={LLM}` edge onto both `llm` providers. If
`run_script()` ran inline and its first `await self._say(...)` blocked waiting on a
reply from `agent`, that would be `load_plugins` waiting, mid-batch, on a plugin that
hasn't started yet *inside the same call that will eventually start it* — a deadlock.
`create_background_task` lets `on_start` return immediately so the batch keeps
committing; the script's first `await` then resolves once `agent` actually starts,
same as `agent.py`'s own `_answer()` runs as a background task so `respond()` can
return fast.

## Typed capabilities: `Protocol` instead of a bare string

`model.py` and `terse_model.py` now each declare their own copy of the contract:

```python
@runtime_checkable
class LLM(Protocol):
    async def reply(self, text: str, persona: str) -> str: ...


class Model(Plugin):
    def __init__(self) -> None:
        super().__init__(name="model", provides={LLM}, tags={"prose"}, ...)
```

`agent.py` declares the same shape and does `requires={LLM}`,
`get_capability(LLM, tag=self.config("model_tag"))`. Neither file imports the other's
`LLM` — the two are structurally identical, and that's the point: the capability name
is *derived* from the type (`LLM` → `"llm"`), checked structurally at admission, not by
identity. It's the same "downloaded, not imported" idea behind every capability in this
series — a string name worked fine when nothing needed to verify shape, and now
something does.

## `capability_access="sealed"`

`host.py` boots the whole graph under `Core(capability_access="sealed", ...)`. Sealing
tightens two things beyond the default `"open"` mode:

- **What a plugin can ask for at all.** `get_capability()` is gated to the plugin's own
  `requires` set (plus anything in `resolves=`, below) — asking for something outside
  that grant raises `CapabilityAccessError`, whether the name is a string or a
  `Protocol` type.
- **What a *typed* lookup gives back.** `agent.py`'s `get_capability(LLM, tag=...)`
  doesn't return the live `Model` instance under `"sealed"` — it returns a
  `CapabilityFacet`: an object that forwards only the methods `LLM` declares (`reply`,
  here — nothing else on `Model` is reachable through it), re-resolves the live
  provider on every call (so a hot-swapped `terse_model.py` rebinds transparently, and
  a revoked one raises `StalePluginError` on the next call), and refuses to let any
  method's return value leak a live plugin or `Core` handle back out (the "sealed
  return guard"). None of this shows up in the transcript — `self.llm.reply(...)`
  reads identically to calling the real method — but every call now passes through
  that guard instead of touching `Model` directly.

An *untyped* string lookup under `"sealed"` still returns the raw provider — there's no
Protocol to attenuate to. Typing the capability is what buys the facet.

## `resolves=`: a grant without a registration promise

Nothing in this program's final graph actually needs `resolves=` — every capability
each plugin touches is already in that plugin's own `requires`. It's worth knowing
about anyway, because `"sealed"`'s gate checks `requires ∪ resolves`, and the two mean
different things: `requires` is validated at registration — the capability must exist
in the graph already, or registration fails outright. `resolves` is deliberately
**not** — a plugin can name a capability there that has no provider yet, to authorize a
lookup it expects to succeed only *later*:

```python
class Dashboard(Plugin):
    def __init__(self) -> None:
        super().__init__(name="dashboard", resolves={"metrics"})

    async def on_start(self) -> None:
        # "metrics" isn't required — dashboard runs fine without it. But if some
        # other plugin loads later and starts providing it, sealed mode already
        # authorizes this plugin to ask.
        self.metrics = None
        with contextlib.suppress(CapabilityError):
            self.metrics = await self.get_capability("metrics")
```

Under `"open"` this distinction doesn't matter — anything resolves. Under `"sealed"`
it's the difference between "this plugin might reach for X later" being declared
up front, in the manifest, versus silently failing every time until the day X exists.

## `host.py`: the rollback recipe, and waiting for shutdown

```python
async def build_host(core: Core) -> tuple[str, ...]:
    try:
        return await core.load_plugins(_boot_sources())
    except BatchLoadError as e:
        for name in reversed(e.installed):  # () on a plan-phase fault → no-op
            await core.unregister_plugin(name)
        raise
```

`load_plugins` commits the whole folder or nothing — but "nothing" needs qualifying.
`BatchLoadError.installed` is the tuple of names that *did* commit before the failure,
in commit order; on a fault caught during planning (a cycle, a missing capability, a
source that fails to compile) it's empty and the loop is a no-op, but a fault raised
by a candidate's own `on_start()` partway through commit leaves a real prefix live. The
recipe above unwinds that prefix in reverse before re-raising, so a bad batch never
leaves a partial graph running. This isn't demonstrated live in this tutorial — it's
exercised directly in the repository's own acceptance suite
(`tests/test_example_host.py`), which breaks a required config value on purpose and
asserts the graph ends up empty again.

The other change in `main()`:

```python
    async with Core(capability_access="sealed", plugin_configs=host_configs()) as core:
        await build_host(core)
        shutdown = await core.get_capability("shutdown_handling")
        await shutdown.wait_for_shutdown()
```

Tutorial 5 named the gap plainly: "Ctrl-C raises a bare `KeyboardInterrupt` — there's no
graceful shutdown yet." `shutdown.py` closes it. `host.py` resolves it through the same
capability surface every plugin uses — it holds no special access, just a name in its
own `requires`... except `host.py` isn't a plugin, so it has no `requires` at all, and
under `"sealed"` that would ordinarily be a problem. It isn't one here because `Core`
itself is never subject to its own access policy — only plugins are gated by
`capability_access`. The host is the one place in the whole program still holding the
real `Core`.

## `shutdown.py`: signals and a bus event, converging on one wait

```python
class ShutdownHandler(Plugin):
    def __init__(self) -> None:
        super().__init__(name="shutdown_handler", provides={"shutdown_handling"})
        self._shutdown_event = asyncio.Event()

    async def on_start(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in _SHUTDOWN_SIGNALS:
            loop.add_signal_handler(sig, self._on_signal, sig)

    def _on_signal(self, sig: signal.Signals) -> None:
        asyncio.get_event_loop().create_task(
            self.emit("system.shutdown", {"source": "signal", "signal": sig.name})
        )
        self._shutdown_event.set()

    @event("system.shutdown")
    async def _on_shutdown_event(self, ev: Event) -> None:
        self._shutdown_event.set()

    async def wait_for_shutdown(self) -> None:
        await self._shutdown_event.wait()
```

Two paths converge on the same `asyncio.Event`: an OS signal (Ctrl-C, or `SIGTERM` from
a process manager) sets it directly *and* emits `"system.shutdown"`, so any plugin —
not only the OS — can ask for a clean stop by emitting that event itself. `on_stop`
removes the signal handlers, so the trap is live exactly while this plugin is
registered. Windows has no `loop.add_signal_handler`; the real file bridges
`signal.signal`'s off-loop callback onto the loop with `call_soon_threadsafe` instead —
see [Shut down gracefully](../how-to/how-to-shut-down-gracefully.md) for that path in
full.

Once `wait_for_shutdown()` returns, `async with Core(...)` exits and `core.stop()`
tears the graph down in reverse dependency order — the same teardown `roster.py` has
been narrating since tutorial 6, now triggered by Ctrl-C instead of the process just
ending.

## Run it

```bash
python -m chat.host
```

```text
roster: + roster
roster: + shutdown_handler (provides shutdown_handling)
roster: + supervisor
roster: + terse_model (provides llm)
roster: + watcher
roster: + agent
user:  hello there
agent: Cheerfully #1: you said 'hello there'.
...[hot-reloaded the persona]...
user:  what's the weather like?
roster: ~ persona hot-swapped
agent: Grumpily #2: you said 'what's the weather like?'.
roster: 9 plugins live; capabilities: llm, shutdown_handling
conversation done — Ctrl-C to exit (edit grumpy_persona.py meanwhile!)
```

`conductor`, `model`, and `persona` don't appear in that list — they commit
alphabetically *before* `roster`, so `roster.py`'s own `on_start` snapshots them
silently instead of printing their arrival; only registrations after that point get
the `+` line. Press Ctrl-C and the graph tears down cleanly:

```text
roster: - agent
roster: - watcher
roster: - terse_model
roster: - supervisor
roster: - shutdown_handler
roster: ~ capability shutdown_handling revoked
```

— then the process exits with no traceback. The folder watcher is still live for as
long as the process runs: edit `persona.py` (not `grumpy_persona.py`, already swapped
in) and save, and you'll see the same live hot-reload tutorial 5 demonstrated, right up
until you press Ctrl-C.

## The key idea

Sealing, typed capabilities, `resolves=`, and graceful shutdown aren't new kernel
machinery — they're the same primitives this whole series has used, pointed at a
stricter question: not just "what can a plugin do," but "what is a plugin *allowed* to
ask for, and how honestly does the kernel enforce that boundary." Downloaded policy
goes all the way down, including who's allowed to ask for what — the kernel provides
the gate and the facet; deciding where the gate sits is still policy, not core.

## Series closed

`chat/` now matches
[`examples/example_host/`](https://github.com/hiddenfalls42/uxok/tree/main/examples/example_host)
byte-for-byte. From here:

- [Architecture overview](../explanation/architecture-overview.md) — the capability
  system section covers `CapabilityFacet`, sealed mode, and the return guard in more
  depth
- [Shut down gracefully](../how-to/how-to-shut-down-gracefully.md) — the cross-platform
  signal-handling path `shutdown.py` takes for granted here
- [Work with capabilities](../how-to/how-to-work-with-capabilities.md) — the full
  `requires`/`resolves`/`capability_access` reference
- [Boot a plugin graph in order](../how-to/how-to-boot-a-plugin-graph-in-order.md) —
  `BatchLoadError`, `try_load_plugins`, and the rest of the batch-boot contract
