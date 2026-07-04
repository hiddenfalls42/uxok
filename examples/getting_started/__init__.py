"""Getting-started example: the README quick-start as a proper modular host.

Two plugins, each in its own module, composed by a ``host`` that hot-loads both
from source — the minimal counterpart to ``example_host/``. Run it with
``python -m examples.getting_started.host``; ``tests/test_getting_started.py`` is
its acceptance suite.

Deliberately import-free: the host loads ``model.py`` and ``agent.py`` from
*source*, never as modules — the tutorial's point — so this package exports
nothing that would import them as a side effect.
"""
