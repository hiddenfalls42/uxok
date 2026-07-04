`example_host/` is the destination of the tutorial series: a small
conversational-agent program, one plugin per module, that wires the kernel features
a real host leans on — batch source-loading boot (`core.load_plugins`, importing no
plugin class, all or nothing — with `build_host_best_effort` showing the best-effort
`core.try_load_plugins` alternative), a cid-correlated conversation over the event bus (no
sleeps), two competing **typed** `llm` providers selected by tag from config
(`ConfigField`/`REQUIRED`), a stateful `persona` hook whose reply count survives
**hot reload** (`get_state`/`restore_state`), a watcher that hot-loads edited plugin
files from disk, a roster mirroring every graph change, a supervisor consuming the
kernel's error signals, and graceful shutdown — all under
`capability_access="sealed"`.

Run it with `python -m examples.example_host.host`; `tests/test_example_host.py`
is its acceptance suite and runs the whole graph under all three
`capability_access` modes.

`getting_started/` is the minimal counterpart — the same Model / Agent / persona-hook
conversation, two plugins and a host that **hot-loads both from source** (importing
neither plugin class) and self-terminates on a completion event. It is the package
the [Getting started](../docs/tutorials/getting-started.md) tutorial walks through,
run with `python -m examples.getting_started.host` and covered by
`tests/test_getting_started.py`. Start there; reach for `example_host/` when you
want live hot-*reload* (swapping a running plugin) and graceful signal shutdown.

More examples are planned as time allows — otherwise the docs cover each primitive
in depth.
