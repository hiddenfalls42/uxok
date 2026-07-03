"""Unit tests for the @hook, @event, and discover_decorated_methods decorators."""


class TestHookDecorator:
    """Test the @hook decorator metadata attachment."""

    def test_hook_basic_metadata(self):
        from uxok.plugin._decorators import hook

        @hook("data.validate", priority=5)
        async def validate(self, data):
            return data

        assert hasattr(validate, "_orion_hooks")
        info = validate._orion_hooks[0]
        assert info["name"] == "data.validate"
        assert info["priority"] == 5


class TestOnDecorator:
    """Test the @on decorator metadata attachment."""

    def test_on_basic(self):
        from uxok.plugin._decorators import event

        @event("user.created")
        async def handler(self, event):
            pass

        info = handler._orion_event_handlers[0]
        assert info["pattern"] == "user.created"


class TestDiscoverDecoratedMethods:
    """Test discover_decorated_methods introspection."""

    def test_discover_hooks(self):
        from uxok.plugin._decorators import discover_decorated_methods, hook

        class FakePlugin:
            @hook("my.hook", priority=5)
            async def handle(self):
                pass

        hooks, events = discover_decorated_methods(FakePlugin())
        assert "my.hook" in hooks
        assert len(hooks["my.hook"]) == 1
        assert hooks["my.hook"][0][1] == 5  # priority

    def test_discover_events(self):
        from uxok.plugin._decorators import discover_decorated_methods, event

        class FakePlugin:
            @event("user.created")
            async def handle(self, event):
                pass

        hooks, events = discover_decorated_methods(FakePlugin())
        assert "user.created" in events
        assert events["user.created"][0]["method"] is not None

    def test_decorated_private_methods_are_discovered(self):
        """An explicit @hook on a single-underscore method registers it.

        Silently ignoring an explicit decorator would be surprising; only
        dunder methods are skipped during discovery.
        """
        from uxok.plugin._decorators import discover_decorated_methods, hook

        class FakePlugin:
            @hook("private.hook")
            async def _internal(self):
                pass

        hooks, events = discover_decorated_methods(FakePlugin())
        assert "private.hook" in hooks

    def test_old_format_string_handler(self):
        """Legacy handler_info as plain strings should still work."""
        from uxok.plugin._decorators import discover_decorated_methods

        class FakePlugin:
            async def old_handler(self, event):
                pass

        # Manually set old-format marker (plain string, not dict)
        FakePlugin.old_handler._orion_event_handlers = ["legacy.event"]

        hooks, events = discover_decorated_methods(FakePlugin())
        assert "legacy.event" in events
        assert events["legacy.event"][0]["method"] is not None
