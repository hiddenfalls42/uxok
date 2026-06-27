# uxok

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

> **Status: 0.x, pre-1.0.** Functional and well-tested, but the public API is still settling and may change between releases. If you build on it now, pin to a commit and expect to adjust on upgrade.

**A hot-loading microkernel for Python — minimal core, everything else is a plugin.**

I made this python kernel for prototyping self-coding agentic applications that interface with physical components, but it's not limited to that. It started as an experiment with early agentic coding to make a simple library and evolved into a personal passion and a lot of learning along the way.

uxok (mu-xok) has a microkernel-style architecture, taking inspiration from the 1990's exokernel project "xok". The core provides only essential primitives for building extensible, self-modifying agentic applications: event-driven communication, hook-based extension points, hot-loading plugins with lifecycle management, and capability-based dependency resolution.

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
Use `mkdocs serve` to preview the site live instead of building it.

Full docs include tutorials, how-to guides, architecture deep-dives, and API reference.

## Contributing

Contributions welcome! 

## License

MIT — see [LICENSE](LICENSE)
