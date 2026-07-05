# Tutorials

Tutorials lead you through complete, hands-on lessons that end in a working, runnable result. The tutorials in this section teach the uxok patterns that matter most by having you build something real — not by explaining concepts in the abstract, but by walking you through code that you can run and verify yourself. Start here if you have not used uxok before, and work through them in order — each one builds on the last.

## In this section

- [Getting started](getting-started.md) — Build a properly structured uxok project: two plugins in their own modules, a host that composes them, and a capability wired between them by name — the layout a real project uses, not a single-file script.
- [Persona hot-reload](hot-reload.md) — Extract a hook into its own plugin, then swap it for another while the program runs — no restart, no lost state.
- [Configuration and tagged providers](configuration.md) — Add a second, competing provider of the same capability, and let config pick which one a plugin talks to.
- [Deterministic conversations](deterministic-conversations.md) — Replace a fixed turn loop with correlated request/reply, so each question gets its own matching answer.
- [Time and the tick](time-and-the-tick.md) — Add a folder watcher that hot-reloads changed plugin files on its own schedule, using the kernel's logical clock.
- [Watching the graph](watching-the-graph.md) — Add a roster that mirrors the live plugin registry and a supervisor that evicts repeat offenders, both built from the same hook and event primitives.
- [Locking it down](locking-it-down.md) — Seal capability access, type the `llm` capability as a `Protocol`, add the `BatchLoadError` rollback recipe, and shut down gracefully — the series' final stage.
