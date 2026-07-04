"""Example host: a small conversational-agent program built on the uxok kernel.

The extended counterpart to the minimal ``getting_started/`` example — the same
hot-loading host shape (``build_host`` batch-loads every plugin module from
source via ``core.load_plugins``, importing none of them), grown into a fuller
program. A ``user.says`` event reaches an ``Agent`` that answers through an
``llm`` capability it never imports, in a voice supplied by the ``persona``
hook — which the host hot-reloads mid-run to change the agent's tone with zero
downtime — and a ``ShutdownHandler`` keeps the program alive until a signal or
a ``system.shutdown`` event. Run it with ``python -m examples.example_host.host``;
``tests/test_example_host.py`` is its acceptance suite.

Deliberately import-free: the host loads every plugin module from *source*
(``grumpy_persona.py`` only later, live), so this package imports none of them
either.
"""
