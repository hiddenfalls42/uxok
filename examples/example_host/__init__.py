"""Example host: a small conversational-agent program built on the uxok kernel.

The extended counterpart to the minimal ``getting_started/`` example (and the
README quick-start it mirrors): start there, reach here for the fuller host. A ``user.says`` event
reaches an ``Agent`` that answers through an ``llm`` capability it never imports,
in a voice supplied by the ``persona`` hook — which the host hot-reloads mid-run
to change the agent's tone with zero downtime. It wires the kernel primitives a
real program leans on: the event bus, a hook extension point, a capability
provider/consumer, ordered boot with capability polling, hot reload, and graceful
shutdown. See ``host.py`` for the composition and
``python -m examples.example_host.host`` to run it.
"""

# Only the plugin classes are re-exported here. ``build_host``/``main`` live in
# ``examples.example_host.host`` and are imported from there directly — importing the
# ``host`` submodule into this package __init__ would trip a runpy warning under
# ``python -m examples.example_host.host``. ``grumpy_persona`` is intentionally not
# imported: the host loads it from source at runtime, not as a Python import.
from examples.example_host.agent import Agent
from examples.example_host.model import Model
from examples.example_host.persona import Persona
from examples.example_host.shutdown import ShutdownHandler

__all__ = [
    "Agent",
    "Model",
    "Persona",
    "ShutdownHandler",
]
