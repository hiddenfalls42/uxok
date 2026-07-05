"""Example host: a small conversational-agent program built on the uxok kernel.

The destination of the tutorial series — the minimal ``getting_started/``
example grown, one feature per stage, into the program a real host resembles.
``host.py`` batch-loads every plugin module from source via
``core.load_plugins`` (all or nothing) and waits for shutdown; it drives none
of the plugins itself. The conductor plugin drives the demo conversation, two
competing typed ``llm`` providers are selected by tag from config, a stateful
persona's reply count survives hot-swap, a watcher hot-loads edited plugin
files from disk, a roster mirrors every graph change, a supervisor consumes
the kernel's error signals, and shutdown is graceful — all under
``capability_access="sealed"``.

Run it with ``python -m examples.example_host.host``;
``tests/test_example_host.py`` is its acceptance suite.

Deliberately import-free: the host loads every plugin module from *source*
(``grumpy_persona.py`` only later, live), so this package imports none of them
either.
"""
