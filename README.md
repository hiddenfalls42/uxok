# uxok

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

> **Status: 0.x, pre-1.0.** Functional and well-tested, but the public API is still settling and may change between releases. See [Status](#status).

**A hot-loading microkernel for Python — minimal core, everything else is a plugin.**

uxok provides a kernel-style architecture with essential primitives for building extensible applications: event-driven communication, hook-based extension points, plugin lifecycle management, and capability-based dependency resolution. Everything beyond these core primitives is implemented as plugins.

uxok takes its name and its instincts from the MIT exokernel: a minimal core that provides **mechanism, not policy**, with management pushed out into replaceable plugins, and resources acquired through **secure bindings** that authorize once and are cheap to use. It deliberately does **not** implement hardware-grade protection between mutually distrustful principals — plugins share a process and a trust domain. The borrowed ideas are the binding discipline, visible revocation, and downloaded policy; the omission is the protection boundary.

## Status

uxok is **0.x — pre-1.0**. It is functional and well-tested (900+ tests), but the public API is still settling and **may change between releases**. The goal is a frozen, "constitutional" API that locks at 1.0; that lock has not happened yet, so treat today's contracts as stabilizing, not final.

- **Solid** — the core primitives (event bus, hooks, registry, capability system, plugin base) work and are covered by a large test suite.
- **In flux** — exact API shapes, some names, and parts of the docs may change before 1.0.
- **Not on PyPI yet** — install from source (see [Development](#development)).

If you build on it now, pin to a commit and expect to adjust on upgrade.

## Features

- **Hot-Loading** — Add/remove/replace plugins at runtime without restart
- **Event-Driven** — Non-blocking pub/sub messaging for loose coupling
- **Hook System** — Priority-based extension points for pipelines
- **Capability System** — Kernel-style dependency resolution with tag-based selection
- **Frozen records** — Protocols and event/hook payloads are immutable at runtime; behavior changes by hot-swapping plugins, not by mutating live objects
- **Type Safety** — Full protocol-based typing with mypy support

## Development 

```bash
git clone https://github.com/hiddenfalls42/uxok.git
cd uxok
pip install -e .[dev]

pytest                  # Run tests
ruff check src tests   # Lint
mypy src               # Type check
```

There is no packaged release yet — install from source as above. A virtualenv is strongly recommended.
## Documentation

**Build locally:**
```bash
pip install -e ".[docs]"
mkdocs build --strict  # Output in ./site/
```
`mkdocs build` writes the static site to `./site/`; use `mkdocs serve` to preview it live at a local address.

Full docs include tutorials, how-to guides, architecture deep-dives, and API reference.

## Contributing

Contributions welcome! 
## License

MIT — see [LICENSE](LICENSE)
