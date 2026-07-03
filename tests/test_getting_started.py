"""Getting-started example: the minimal modular host on kernel primitives.

This suite is the acceptance test for ``examples/getting_started/``. It drives the
same ``build_host`` the runnable program uses, so the tutorial's program and the
tested program never drift. The whole suite runs under both
``capability_access="open"`` and ``"declared"`` so the modular graph is proven
under the enforced secure-capability mode, not just the permissive one.
"""

import asyncio
import re
from pathlib import Path

import pytest
import pytest_asyncio
from examples.getting_started.host import build_host

from uxok import Core
from uxok.protocols import CoreState

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TUTORIAL = _REPO_ROOT / "docs" / "tutorials" / "getting-started.md"
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "getting_started"

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

    async def _stop(_ev):
        done.set()

    await core.events.subscribe("conversation.over", _stop)
    await build_host(core)  # hot-loads plugin modules from source
    await asyncio.wait_for(done.wait(), timeout=2.0)

    printed = capsys.readouterr().out.splitlines()
    assert printed == _EXPECTED


def test_tutorial_code_blocks_match_example_files():
    """The tutorial's three python blocks are byte-identical to the example modules.

    This is the sync guard the tutorial promises ("kept in sync … by
    tests/test_getting_started.py"): edit either side without the other and it fails.
    """
    blocks = re.findall(r"```python\n(.*?)```", _TUTORIAL.read_text(), flags=re.DOTALL)
    modules = ["model.py", "agent.py", "host.py"]
    assert len(blocks) == len(modules), (
        f"expected {len(modules)} python blocks in the tutorial, found {len(blocks)}"
    )
    for block, name in zip(blocks, modules, strict=True):
        assert block == (_EXAMPLE_DIR / name).read_text(), (
            f"tutorial code block for {name} differs from examples/getting_started/{name}"
        )
