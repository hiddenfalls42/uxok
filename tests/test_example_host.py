"""Example host: the conversational-agent reference graph on kernel primitives.

This suite is also the acceptance test for the example: every kernel feature the
host demonstrates is exercised here — the event bus (``user.says`` → ``agent.says``),
the hook extension point (``persona``), the capability surface (``get_capability``),
ordered boot (``build_host``), hot reload
(``core.load_plugin`` swapping the persona), and graceful shutdown
(``system.shutdown``). The whole suite runs under both ``capability_access="open"``
and ``"declared"`` so the graph is proven under the enforced secure-capability
mode, not just the permissive one.

The conversation is driven by publishing ``user.says`` events directly and draining
fire-and-forget dispatch, so assertions are deterministic.
"""

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from examples.example_host import Agent, Model, ShutdownHandler
from examples.example_host.host import build_host

from uxok import Core
from uxok.protocols import CoreState, Event

_GRUMPY = Path(__file__).resolve().parents[1] / "examples" / "example_host" / "grumpy_persona.py"


@pytest_asyncio.fixture(params=["open", "declared"])
async def core(request):
    """A fresh core under each capability_access mode, with guaranteed cleanup."""
    c = Core(capability_access=request.param)
    try:
        yield c
    finally:
        if c.state is CoreState.RUNNING:
            await c.stop()


async def _drain(seconds: float = 0.15):
    """Let fire-and-forget event dispatch (and its nested emits) settle."""
    await asyncio.sleep(seconds)


async def _replies(core) -> list[str]:
    """Subscribe to agent.says and collect reply text into a list."""
    out: list[str] = []

    async def on_reply(ev: Event) -> None:
        out.append(ev.data["text"])

    await core.events.subscribe("agent.says", on_reply)
    return out


# ---------------------------------------------------------------------------
# Composition + ordered boot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_host_registers_graph_and_exposes_capabilities(core):
    shutdown = await build_host(core)

    assert isinstance(shutdown, ShutdownHandler)
    # The model provider is resolvable through the capability surface the moment
    # build_host returns — providers are registered before the agent that needs them.
    assert await core.get_capability("llm") is not None


# ---------------------------------------------------------------------------
# Event bus + hook extension point + capability surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_message_gets_a_persona_voiced_reply(core):
    await build_host(core)
    replies = await _replies(core)

    await core.events.publish(Event("user.says", {"text": "hello there"}))
    await _drain()

    assert replies == ["Cheerfully: you said 'hello there'."]


@pytest.mark.asyncio
async def test_agent_works_without_a_persona_handler(core):
    """The persona hook is a genuine opt-in: with no handler the reply still goes out."""
    await core.register_plugin(Model())
    await core.register_plugin(Agent())  # no Persona registered
    replies = await _replies(core)

    await core.events.publish(Event("user.says", {"text": "hi"}))
    await _drain()

    # firstresult with no handler yields None -> it simply prefixes the reply.
    assert replies == ["None you said 'hi'."]


# ---------------------------------------------------------------------------
# Hot reload — swap the persona live
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hot_reloading_the_persona_changes_the_voice(core):
    await build_host(core)
    replies = await _replies(core)

    await core.events.publish(Event("user.says", {"text": "first"}))
    await _drain()

    # Zero-downtime swap from the sibling module's source (same plugin name).
    await core.load_plugin(_GRUMPY.read_text(), origin=str(_GRUMPY))

    await core.events.publish(Event("user.says", {"text": "second"}))
    await _drain()

    assert replies == [
        "Cheerfully: you said 'first'.",
        "Grumpily: you said 'second'.",
    ]


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_event_unblocks_wait(core):
    handler = ShutdownHandler()
    await core.register_plugin(handler)

    waiter = asyncio.create_task(handler.wait_for_shutdown())
    await asyncio.sleep(0)
    assert not waiter.done()

    # Any plugin emitting system.shutdown unblocks the host loop.
    await core.events.publish(Event("system.shutdown", {"source": "test"}))
    await asyncio.wait_for(waiter, timeout=1.0)
    assert waiter.done()
