# uxok

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

> This program is in early development, expect problems.

**A hot-loading microkernel for Python — minimal core, everything else is a plugin.**

uxok provides a kernel-style architecture with essential primitives for building extensible applications: event-driven communication, hook-based extension points, plugin lifecycle management, and capability-based dependency resolution. Everything beyond these core primitives is implemented as plugins.

uxok takes its name and its instincts from the MIT exokernel: a minimal core that provides **mechanism, not policy**, with management pushed out into replaceable plugins, and resources acquired through **secure bindings** that authorize once and are cheap to use. It deliberately does **not** implement hardware-grade protection between mutually distrustful principals — plugins share a process and a trust domain. The borrowed ideas are the binding discipline, visible revocation, and downloaded policy; the omission is the protection boundary.

## Features

- **Hot-Loading** — Add/remove/replace plugins at runtime without restart
- **Event-Driven** — Non-blocking pub/sub messaging for loose coupling
- **Hook System** — Priority-based extension points for pipelines
- **Capability System** — Kernel-style dependency resolution with tag-based selection
- **Immutable Contracts** — Frozen protocols and event/hook records: the API never churns, and behavior changes by hot-swapping plugins, not mutating them
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

No way to install but with development installation yet. 
A venv is highly recommended. 
## Documentation

**Build locally:**
```bash
pip install -e ".[docs]"
mkdocs build --strict  # Output in ./site/
```
This serves the docs as a website at a local address. You can also just open them in Obsidian but links don't work right. 

Full docs include tutorials, how-to guides, architecture deep-dives, and API reference.

## Contributing

Contributions welcome! 
## License

MIT — see [LICENSE](LICENSE)
