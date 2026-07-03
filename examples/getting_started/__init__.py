"""Getting-started example: the README quick-start as a proper modular host.

Two plugins, each in its own module, composed by a ``host`` that hot-loads both
from source — the minimal counterpart to ``example_host/``. Run it with
``python -m examples.getting_started.host``; ``tests/test_getting_started.py`` is
its acceptance suite.
"""

# Only the plugin classes are re-exported. ``build_host``/``main`` live in
# ``host`` and are imported from there directly — importing the ``host`` submodule
# into this package __init__ would trip a runpy warning under
# ``python -m examples.getting_started.host``.
from .agent import Agent
from .model import Model

__all__ = ["Agent", "Model"]
