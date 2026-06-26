"""Unit tests for the @handle_errors decorator."""

import asyncio
import functools
from unittest.mock import patch

import pytest

from uxok import Plugin
from uxok.plugin import handle_errors


class MockPlugin:
    """Mock plugin class for testing decorators."""

    def __init__(self):
        self.events = []
        self.sync_events = []

    async def emit(self, event_name, data):
        """Mock async emit method that records events."""
        self.events.append((event_name, data))

    def emit_sync(self, event_name, data):
        """Mock sync emit method that records events."""
        self.sync_events.append((event_name, data))


class TestHandleErrors:
    """Test cases for @handle_errors decorator."""

    def test_handle_errors_async_success(self):
        """Test that successful async methods work normally."""

        class DemoPlugin(MockPlugin):
            @handle_errors(emit_event=False)
            async def successful_method(self, value):
                return value * 2

        plugin = DemoPlugin()
        result = asyncio.run(plugin.successful_method(5))
        assert result == 10
        assert len(plugin.events) == 0  # No error events emitted

    def test_handle_errors_async_success_second(self):
        """Test that another successful async method works normally."""

        class DemoPlugin(MockPlugin):
            @handle_errors(emit_event=False)
            async def successful_method(self, value):
                return value * 3

        plugin = DemoPlugin()
        result = asyncio.run(plugin.successful_method(5))
        assert result == 15
        assert len(plugin.events) == 0  # No error events emitted

    def test_handle_errors_async_exception_with_event(self):
        """Test error handling in async methods with event emission."""

        class DemoPlugin(MockPlugin):
            @handle_errors(emit_event=True, return_on_error="error_value")
            async def failing_method(self, value):
                if value == 0:
                    raise ValueError("Test error")
                return value * 2

        plugin = DemoPlugin()
        result = asyncio.run(plugin.failing_method(0))

        assert result == "error_value"
        assert len(plugin.events) == 1
        event_name, event_data = plugin.events[0]
        assert event_name == "plugin.error"
        assert event_data["plugin"] == "demo"  # derived from DemoPlugin class name
        assert event_data["method"] == "failing_method"
        assert event_data["error"] == "Test error"
        assert event_data["error_type"] == "ValueError"
        assert "timestamp" in event_data

    def test_handle_errors_async_exception_with_event_second(self):
        """Test error handling in async methods with event emission (async emit)."""

        class DemoPlugin(MockPlugin):
            @handle_errors(emit_event=True, return_on_error="async_error")
            async def failing_method(self, value):
                if value == 0:
                    raise RuntimeError("Async test error")
                return value * 3

        plugin = DemoPlugin()
        result = asyncio.run(plugin.failing_method(0))

        assert result == "async_error"
        assert len(plugin.events) == 1
        event_name, event_data = plugin.events[0]
        assert event_name == "plugin.error"
        assert event_data["error"] == "Async test error"
        assert event_data["error_type"] == "RuntimeError"

    def test_handle_errors_no_event_emission(self):
        """Test that errors don't emit events when emit_event=False."""

        class DemoPlugin(MockPlugin):
            @handle_errors(emit_event=False, return_on_error=None)
            async def failing_method(self):
                raise ValueError("No event test")

        plugin = DemoPlugin()
        result = asyncio.run(plugin.failing_method())

        assert result is None
        assert len(plugin.events) == 0

    def test_handle_errors_custom_return_value(self):
        """Test custom return value on error."""

        class DemoPlugin(MockPlugin):
            @handle_errors(emit_event=False, return_on_error=999)
            async def failing_method(self):
                raise ValueError("Custom return test")

        plugin = DemoPlugin()
        result = asyncio.run(plugin.failing_method())

        assert result == 999

    def test_handle_error_log_levels(self):
        """Test different log levels for error handling."""

        # Test ERROR level (default)
        with patch("uxok.plugin._decorators.logger") as mock_logger:

            class DemoPlugin(MockPlugin):
                @handle_errors(emit_event=False, log_level="ERROR")
                async def error_method(self):
                    raise ValueError("Error log test")

            plugin = DemoPlugin()
            asyncio.run(plugin.error_method())
            mock_logger.error.assert_called_once()

        # Test WARNING level
        with patch("uxok.plugin._decorators.logger") as mock_logger:

            class DemoPlugin(MockPlugin):
                @handle_errors(emit_event=False, log_level="WARNING")
                async def warning_method(self):
                    raise ValueError("Warning log test")

            plugin = DemoPlugin()
            asyncio.run(plugin.warning_method())
            mock_logger.warning.assert_called_once()

        # Test INFO level
        with patch("uxok.plugin._decorators.logger") as mock_logger:

            class DemoPlugin(MockPlugin):
                @handle_errors(emit_event=False, log_level="INFO")
                async def info_method(self):
                    raise ValueError("Info log test")

            plugin = DemoPlugin()
            asyncio.run(plugin.info_method())
            mock_logger.info.assert_called_once()

    def test_handle_error_no_emit_method(self):
        """Test error handling when plugin has no emit method."""

        class NoEmitPlugin:
            @handle_errors(emit_event=True, return_on_error="no_emit")
            async def failing_method(self):
                raise ValueError("No emit method test")

        plugin = NoEmitPlugin()
        result = asyncio.run(plugin.failing_method())

        assert result == "no_emit"
        # Should not raise an exception even though emit_event=True

    def test_handle_errors_preserves_signature(self):
        """Test that decorator preserves original function signature."""

        class DemoPlugin(MockPlugin):
            @handle_errors()
            async def test_method(self, arg1: str, arg2: int = 10) -> str:
                return f"{arg1}_{arg2}"

        # Check that signature is preserved
        import inspect

        sig = inspect.signature(DemoPlugin.test_method)
        params = list(sig.parameters.keys())
        assert params == ["self", "arg1", "arg2"]
        assert sig.parameters["arg2"].default == 10

    def test_handle_errors_composition(self):
        """Test that handle_errors works with other decorators."""

        def async_decorator(func):
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                result = await func(*args, **kwargs)
                return f"decorated_{result}"

            return wrapper

        class DemoPlugin(MockPlugin):
            @handle_errors(return_on_error="failed")
            @async_decorator
            async def test_method(self, value):
                if value < 0:
                    raise ValueError("Composition test")
                return str(value)

        plugin = DemoPlugin()

        # Test successful case
        result = asyncio.run(plugin.test_method(5))
        assert result == "decorated_5"

        # Test error case
        result = asyncio.run(plugin.test_method(-1))
        assert result == "failed"


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


class TestHandleErrorsEmitFailure:
    """Test that a failing emit() does not mask the original error handling."""

    def test_emit_failure_does_not_raise(self):
        class FailEmitPlugin:
            async def emit(self, name, data):
                raise RuntimeError("emit broken")

        class P(FailEmitPlugin):
            @handle_errors(emit_event=True, return_on_error="safe")
            async def work(self):
                raise ValueError("original error")

        plugin = P()
        result = asyncio.run(plugin.work())
        assert result == "safe"


class TestHandleErrorsM12:
    """handle_errors supports sync methods and routes real Plugins to the
    standard core.plugin_error signal (decision #14 / audit M12)."""

    def test_sync_method_stays_sync(self):
        class P(MockPlugin):
            @handle_errors(emit_event=False, return_on_error="fallback")
            def compute(self):
                raise ValueError("sync boom")

        p = P()
        result = p.compute()  # no await — wrapper matched the sync function
        assert not asyncio.iscoroutine(result)
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_real_plugin_emits_core_plugin_error(self, clean_core):
        from uxok.protocols import Event

        core = clean_core

        class Worker(Plugin):
            def __init__(self):
                super().__init__(name="worker")

            @handle_errors(return_on_error="handled")
            async def risky(self):
                raise RuntimeError("guarded boom")

        worker = Worker()
        await core.register_plugin(worker)

        errors = []

        async def on_error(event: Event):
            errors.append(event.data)

        await core.events.subscribe("core.plugin_error", on_error)

        assert await worker.risky() == "handled"
        await asyncio.sleep(0.05)

        assert len(errors) == 1
        assert errors[0]["source"] == "handled_method"
        assert errors[0]["method"] == "risky"
        assert errors[0]["plugin_name"] == "worker"
