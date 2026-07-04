"""host.py — composes the conversational example into a runnable program.

A *host* boots a :class:`~uxok.Core`, hands it a folder of plugin sources, and
keeps it alive. This is the destination of the tutorial series — the
``getting_started/`` host grown the features a real program leans on:

    build_host(core)       batch-load every plugin module via core.load_plugins,
                           keep the whole graph or nothing (BatchLoadError); the
                           best-effort sibling build_host_best_effort uses
                           core.try_load_plugins to boot the loadable subgraph
    say(...)               correlated request/reply — no sleeps: each user line
                           carries a cid and awaits its own agent.says.<cid>
    core.load_plugin(...)  hot-swap the persona mid-conversation (state survives)
    Watcher                hot-loads edited plugin files while it runs
    Roster / Supervisor    observe the graph; consume the kernel's error signals
    ShutdownHandler        trap signals + system.shutdown, drain cleanly

The graph it loads (all from source — the host imports no plugin class):

    user.says.<cid> ──▶ Agent ──hook "persona"──▶ Persona  (hot-swapped ──▶ grumpy)
                          │ requires "llm" (typed, tag from config)
                          ├──▶ Model       tags={"prose"}   ┐ contested
                          └──▶ TerseModel  tags={"terse"}   ┘ capability

``main()`` runs ``capability_access="sealed"`` — every plugin's manifest is the
complete statement of its authority. ``build_host`` is shared by ``main`` and
the test suite, so the running program and the tested program never drift.
Run it as a module: ``python -m <package>.host``.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import sys
from collections.abc import Iterable
from pathlib import Path

from uxok import BatchLoadError, Core
from uxok.protocols import BatchLoadReport, Event

_HERE = Path(__file__).resolve().parent
_HOST_FILES = {"__init__.py", "host.py"}
_SWAP_PAYLOADS = {"grumpy_persona.py"}  # hot-reloaded in later; not part of the boot graph

logger = logging.getLogger("example_host")


def host_configs() -> dict[str, dict[str, object]]:
    """Per-plugin configuration, validated against each plugin's ``config_schema``."""
    return {
        "agent": {"model_tag": "prose"},  # which llm provider the agent asks for
        "watcher": {"watch_dir": str(_HERE)},  # REQUIRED — no default exists
    }


def _boot_sources() -> list[tuple[str, str]]:
    """The ``(code, origin)`` sources for this folder's boot graph.

    ``grumpy_persona.py`` and the host's own files are excluded: the former is the
    hot-reload payload (booting it would collide with ``persona.py`` — two plugins
    named ``persona`` in one batch), the latter are not plugins.
    """
    skip = _HOST_FILES | _SWAP_PAYLOADS
    paths = [path for path in sorted(_HERE.glob("*.py")) if path.name not in skip]
    return [(path.read_text(), str(path)) for path in paths]


async def build_host(core: Core) -> tuple[str, ...]:
    """Load every plugin module in this folder; keep the whole graph or nothing.

    ``core.load_plugins`` works out the commit order from each plugin's declared
    capabilities (both models before the ``Agent`` that requires ``"llm"``), so
    the host names no plugin and no ordering. On failure the ``installed`` prefix
    is unwound in reverse — this host's policy is all or nothing; keeping the
    prefix is the other legitimate choice. For the best-effort alternative — boot
    whatever loads, report the rest — see :func:`build_host_best_effort`.
    """
    try:
        return await core.load_plugins(_boot_sources())
    except BatchLoadError as e:
        for name in reversed(e.installed):  # () on a plan-phase fault → no-op
            await core.unregister_plugin(name)
        raise


async def build_host_best_effort(
    core: Core, *, extra_sources: Iterable[tuple[str, str | None]] = ()
) -> BatchLoadReport:
    """Boot the loadable subgraph and log a line per skipped source.

    The best-effort counterpart to :func:`build_host`: ``core.try_load_plugins``
    commits everything that resolves and returns a :class:`BatchLoadReport`
    instead of raising — one broken or conflicting file cannot empty the boot.
    This is the policy a folder-scanning host of independently authored plugins
    wants (RFC 0010); the shipped ``main()`` keeps the stricter all-or-nothing
    ``build_host`` because its graph is curated and interdependent.

    ``extra_sources`` are appended to the folder scan so a caller can hand in
    files from elsewhere (or a deliberately broken one) and watch it be reported
    rather than fatal — each ``report.skipped`` entry carries the origin, the
    closed-vocabulary ``reason``, and the underlying ``cause``.
    """
    report = await core.try_load_plugins([*_boot_sources(), *extra_sources])
    for skip in report.skipped:
        logger.warning("skipped %s (%s): %s", skip.origin, skip.reason, skip.cause)
    return report


async def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    cids = itertools.count(1)
    pending: dict[str, asyncio.Future[str]] = {}

    async def on_reply(ev: Event) -> None:
        """Resolve the future waiting on this reply's cid (the topic's last segment)."""
        future = pending.pop(ev.name.rsplit(".", 1)[-1], None)
        if future is not None and not future.done():
            future.set_result(ev.data["text"])

    async def say(core: Core, text: str) -> None:
        """Put one user line on the bus and await its correlated reply — no sleeps."""
        cid = f"c{next(cids)}"
        pending[cid] = asyncio.get_running_loop().create_future()
        print(f"user:  {text}")  # noqa: T201 — demo output is the point
        await core.events.publish(Event("user.says", {"cid": cid, "text": text}))
        await asyncio.wait_for(pending[cid], timeout=2.0)

    async with Core(capability_access="sealed", plugin_configs=host_configs()) as core:
        # Subscribe the reply channel before anything can answer.
        reply_sub = await core.events.subscribe("agent.says.*", on_reply)
        await build_host(core)
        # The host holds no plugin instances — it resolves the shutdown handler
        # through the capability surface, the same door the plugins use.
        shutdown = await core.get_capability("shutdown_handling")

        await say(core, "hello there")

        # Hot-swap the persona from a sibling module's source — zero downtime,
        # and its reply count survives via get_state/restore_state. (Editing
        # grumpy_persona.py while the program runs does the same through the
        # watcher — no host involvement at all.)
        grumpy = _HERE / "grumpy_persona.py"
        await core.load_plugin(grumpy.read_text(), origin=str(grumpy))
        print("...[hot-reloaded the persona]...")  # noqa: T201

        await say(core, "what's the weather like?")

        report = await core.hooks.execute("roster.report", firstresult=True)
        print(f"roster: {report}")  # noqa: T201
        await core.events.unsubscribe(reply_sub)  # conversation over; clean up

        print("conversation done — Ctrl-C to exit (edit grumpy_persona.py meanwhile!)")  # noqa: T201
        await shutdown.wait_for_shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
