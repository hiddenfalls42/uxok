"""Getting-started example: the minimal modular host on kernel primitives.

This suite is the acceptance test for ``examples/getting_started/``. It drives the
same ``build_host`` the runnable program uses, so the tutorial's program and the
tested program never drift. The whole suite runs under both
``capability_access="open"`` and ``"declared"`` so the modular graph is proven
under the enforced secure-capability mode, not just the permissive one.
"""

import asyncio

import pytest
import pytest_asyncio
from examples.getting_started.host import build_host

from uxok import Core
from uxok.protocols import CoreState

_EXPECTED = [
    "user:  hello there",
    "agent: Cheerfully: you said 'hello there'.",
    "user:  what's the weather like?",
    "agent: Cheerfully: you said 'what's the weather like?'.",
]


@pytest_asyncio.fixture(params=["open", "declared"])
async def core(request):
    """A fresh core under each capability_access mode, with guaranteed cleanup."""
    c = Core(capability_access=request.param)
    await c.start()
    try:
        yield c
    finally:
        if c.state is CoreState.RUNNING:
            await c.stop()


@pytest.mark.asyncio
async def test_conversation_prints_two_turns(core, capsys):
    done = asyncio.Event()
    await build_host(core, done)
    await asyncio.wait_for(done.wait(), timeout=2.0)

    printed = capsys.readouterr().out.splitlines()
    assert printed == _EXPECTED
