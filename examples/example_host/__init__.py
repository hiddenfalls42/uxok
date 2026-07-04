"""Example host: a small conversational-agent program built on the uxok kernel.

The destination of the tutorial series — the minimal ``getting_started/``
example grown, one feature per stage, into the program a real host resembles.
The same hot-loading shape (``build_host`` batch-loads every plugin module from
source via ``core.load_plugins``, importing none of them, all or nothing) now
carries: two competing typed ``llm`` providers selected by tag from config, a
cid-correlated conversation with no sleeps, a stateful persona whose reply
count survives hot-swap, a watcher that hot-loads edited plugin files from
disk, a roster mirroring every graph change, a supervisor consuming the
kernel's error signals, and graceful signal shutdown — all under
``capability_access="sealed"``. ``build_host_best_effort`` demonstrates the
best-effort boot policy (``core.try_load_plugins``) as a foil to the shipped
all-or-nothing ``build_host``.

Run it with ``python -m examples.example_host.host``;
``tests/test_example_host.py`` is its acceptance suite.

Deliberately import-free: the host loads every plugin module from *source*
(``grumpy_persona.py`` only later, live), so this package imports none of them
either.
"""
