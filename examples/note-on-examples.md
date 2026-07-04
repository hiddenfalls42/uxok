`example_host/` is the modular, extended counterpart to the README quick-start: a
small conversational-agent program, one plugin per module, that wires the kernel
features a real host leans on — the event bus (`user.says` → `agent.says`), a hook
extension point (`persona`), a capability provider/consumer (`llm`, resolved by
name and never imported), batch source-loading boot (`core.load_plugins`, importing
no plugin class), **hot reload** (the host swaps the persona live, from a sibling
module's source), and graceful shutdown.

Run it with `python -m examples.example_host.host`; `tests/test_example_host.py`
is its acceptance suite and runs the whole graph under both `capability_access`
modes.

`getting_started/` is the minimal counterpart — the same Model / Agent / persona-hook
conversation, two plugins and a host that **hot-loads both from source** (importing
neither plugin class) and self-terminates on a completion event. It is the package
the [Getting started](../docs/tutorials/getting-started.md) tutorial walks through,
run with `python -m examples.getting_started.host` and covered by
`tests/test_getting_started.py`. Start there; reach for `example_host/` when you
want live hot-*reload* (swapping a running plugin) and graceful signal shutdown.

More examples are planned as time allows — otherwise the docs cover each primitive
in depth.
