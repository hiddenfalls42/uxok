"""The event bus: publish-subscribe messaging between plugins.

Events are uxok's primary decoupling mechanism. A plugin publishes a named
event and any number of subscribers react to it, with no direct reference
between them. Dispatch is concurrent fire-and-forget: a publish returns
immediately and each subscriber runs as an independent task; ordering is causal,
not global.

The implementation lives in this package's private ``_bus.py`` and is not part
of the public API. What *is* public is spread across two other places by
design, so the parts you reach for as a plugin author stay where they are most
ergonomic. Here is the map:

- **Subscribe** by decorating a plugin method with
  [`@event`][uxok.plugin.event], or at runtime with
  [`Plugin.subscribe`][uxok.plugin.Plugin.subscribe].
- **Publish** with [`Plugin.emit`][uxok.plugin.Plugin.emit] (optionally deferred
  to a future tick).
- **The message** your handler receives is an
  [`Event`][uxok.protocols.events.Event].
- **The bus contract** is the [`EventBus`][uxok.protocols.events.EventBus]
  protocol — depend on it, never on the implementation.

This split mirrors the kernel's curated-flat API: implementations stay private,
the wire types and protocols live under [`uxok.protocols`][uxok.protocols], and
the day-to-day ergonomics surface as methods and decorators on
[`Plugin`][uxok.plugin.Plugin].
"""

# Internal implementation — not part of the public API. The public event
# surface is the @event decorator and Plugin.emit/subscribe (uxok.plugin) plus
# the Event and EventBus contracts (uxok.protocols.events).
