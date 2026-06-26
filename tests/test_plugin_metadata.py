"""Test PluginMetadata with hooks_consumed and events_published fields."""

from uuid import uuid4

import pytest

from uxok.protocols import PluginMetadata


class TestPluginMetadataHooksEvents:
    """Test PluginMetadata accepts hooks_consumed and events_published fields."""

    def test_plugin_metadata_with_hooks_consumed_and_events_published(self):
        """Test PluginMetadata accepts hooks_consumed and events_published."""
        meta = PluginMetadata(
            id=uuid4(),
            name="test_plugin",
            version="1.0.0",
            hooks_consumed=frozenset(["hook1", "hook2"]),
            events_published=frozenset(["event1", "event2"]),
        )

        assert meta.hooks_consumed == frozenset(["hook1", "hook2"])
        assert meta.events_published == frozenset(["event1", "event2"])

    def test_plugin_metadata_defaults_to_empty_frozenset(self):
        """Test hooks_consumed and events_published default to empty frozenset."""
        meta = PluginMetadata(id=uuid4(), name="test_plugin", version="1.0.0")

        assert meta.hooks_consumed == frozenset()
        assert meta.events_published == frozenset()

    def test_plugin_metadata_accepts_various_iterables(self):
        """Test that Plugin class normalizes various iterable types to frozenset."""
        from tests.helpers import StubPlugin
        from uxok import Core

        # Plugin.__init__ normalizes the iterables (StubPlugin forwards kwargs)
        core = Core()
        plugin = StubPlugin(
            name="test",
            hooks_consumed=["hook1", "hook2"],  # list - normalized to frozenset
            events_published={"event1"},  # set - normalized to frozenset
        )

        # Metadata should have frozensets after normalization
        assert isinstance(plugin.metadata.hooks_consumed, frozenset)
        assert isinstance(plugin.metadata.events_published, frozenset)
        assert plugin.metadata.hooks_consumed == frozenset(["hook1", "hook2"])
        assert plugin.metadata.events_published == frozenset({"event1"})

    def test_plugin_metadata_immutability(self):
        """Test that PluginMetadata is frozen and immutable."""
        meta = PluginMetadata(
            id=uuid4(), name="test", version="1.0.0", hooks_consumed=frozenset(["hook1"])
        )

        with pytest.raises(AttributeError):
            meta.hooks_consumed = frozenset(["hook2"])

    def test_plugin_metadata_resolves_field(self):
        """RFC 0002: PluginMetadata carries a `resolves` frozenset, default empty."""
        meta = PluginMetadata(
            id=uuid4(),
            name="test_plugin",
            version="1.0.0",
            requires=frozenset(["dep"]),
            resolves=frozenset(["runtime_a", "runtime_b"]),
        )

        assert meta.resolves == frozenset(["runtime_a", "runtime_b"])
        # Independent of requires — the two sets are distinct facts.
        assert meta.requires == frozenset(["dep"])

        bare = PluginMetadata(id=uuid4(), name="bare", version="1.0.0")
        assert bare.resolves == frozenset()

    def test_plugin_resolves_normalizes_strings_and_protocols(self):
        """RFC 0002: `resolves` accepts a mixed string/Protocol set and reduces it to a
        frozenset of string names, identically to `requires`."""
        from typing import Protocol

        from tests.helpers import StubPlugin
        from uxok import Core

        class FlowRunner(Protocol): ...

        Core()
        plugin = StubPlugin(
            name="resolver",
            resolves=["cap_one", FlowRunner],  # list + Protocol type
        )

        assert isinstance(plugin.metadata.resolves, frozenset)
        # FlowRunner normalizes to "flow_runner"; the string passes through.
        assert plugin.metadata.resolves == frozenset({"cap_one", "flow_runner"})

    def test_plugin_metadata_resolves_immutable(self):
        """RFC 0002: `resolves` is frozen like the other metadata fields."""
        meta = PluginMetadata(id=uuid4(), name="test", version="1.0.0", resolves=frozenset(["x"]))

        with pytest.raises(AttributeError):
            meta.resolves = frozenset(["y"])

    def test_plugin_metadata_with_all_fields(self):
        """Test PluginMetadata with all fields including hooks_consumed and events_published."""
        plugin_id = uuid4()
        meta = PluginMetadata(
            id=plugin_id,
            name="comprehensive_plugin",
            version="2.0.0",
            description="A test plugin with all metadata",
            author="Test Author",
            dependencies=frozenset([uuid4()]),
            requires=frozenset(["capability1", "capability2"]),
            provides=frozenset(["capability3"]),
            hooks_consumed=frozenset(["hook.a", "hook.b", "hook.c"]),
            events_published=frozenset(["event.x", "event.y"]),
        )

        assert meta.id == plugin_id
        assert meta.name == "comprehensive_plugin"
        assert meta.version == "2.0.0"
        assert meta.description == "A test plugin with all metadata"
        assert meta.author == "Test Author"
        assert len(meta.dependencies) == 1
        assert meta.requires == frozenset(["capability1", "capability2"])
        assert meta.provides == frozenset(["capability3"])
        assert meta.hooks_consumed == frozenset(["hook.a", "hook.b", "hook.c"])
        assert meta.events_published == frozenset(["event.x", "event.y"])
