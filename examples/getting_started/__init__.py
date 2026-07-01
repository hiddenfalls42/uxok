"""Getting-started example: the README quick-start as a proper modular host.

The same two-plugin conversation as the README, but each plugin lives in its own
module and a ``host`` module composes them — the structure a real uxok project
uses. The plugins import only the kernel, never each other; the host wires them
in dependency order and runs the conversation to completion.

This is the minimal counterpart to ``example_host/``: two plugins, a persona hook
carried on the model, and a clean self-terminating run. Run it with
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
