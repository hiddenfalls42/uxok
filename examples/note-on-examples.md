`example_host/` is the modular, extended counterpart to the README quick-start: a
small conversational-agent program, one plugin per module, that wires the kernel
features a real host leans on — the event bus (`user.says` → `agent.says`), a hook
extension point (`persona`), a capability provider/consumer (`llm`, resolved by
name and never imported), ordered boot with capability polling, **hot reload** (the
host swaps the persona live, from a sibling module's source), and graceful
shutdown.

Run it with `python -m examples.example_host.host`; `tests/test_example_host.py`
is its acceptance suite and runs the whole graph under both `capability_access`
modes.

`getting_started/` is the minimal counterpart — the same Model / Agent / persona-hook
conversation, two plugins and a host, that self-terminates with checkable output.
It is the package the [Getting started](../docs/tutorials/getting-started.md)
tutorial walks through, run with `python -m examples.getting_started.host` and
covered by `tests/test_getting_started.py`. Start there; reach for `example_host/`
when you want hot reload and graceful shutdown.

More examples are planned as time allows — otherwise the docs cover each primitive
in depth.
