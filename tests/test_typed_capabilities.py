"""Tests for typed capability protocols."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pytest

from uxok import Core, Plugin
from uxok.errors import CapabilityError, PluginError
from uxok.utils import derive_capability_name, get_protocol_methods, normalize_capability_set
from uxok.utils._capability_utils import signature_incompatibility

# ========== Test Protocol Definitions ==========


@runtime_checkable
class Greeting(Protocol):
    """A greeting capability."""

    async def hello(self, name: str = "World") -> str: ...
    async def goodbye(self, name: str) -> str: ...


@runtime_checkable
class GreetingCapability(Protocol):
    """Suffix should be stripped to 'greeting'."""

    async def hello(self, name: str = "World") -> str: ...
    async def goodbye(self, name: str) -> str: ...


@runtime_checkable
class FileStorage(Protocol):
    """Multi-word capability name."""

    async def read(self, path: str) -> bytes: ...
    async def write(self, path: str, data: bytes) -> None: ...


@runtime_checkable
class MathCap(Protocol):
    """'Cap' suffix should also be stripped."""

    async def fibonacci(self, n: int) -> int: ...


class CustomNamed(Protocol):
    """Capability with explicit name override."""

    __capability_name__ = "custom_name"

    async def do_something(self) -> None: ...


# ========== Test Plugins ==========


class GreetingsPlugin(Plugin):
    """Plugin that correctly implements the Greeting protocol."""

    def __init__(self):
        super().__init__(name="greetings", provides={Greeting})

    async def hello(self, name="World"):
        return f"Hello, {name}!"

    async def goodbye(self, name="World"):
        return f"Goodbye, {name}!"


class IncompleteGreetingsPlugin(Plugin):
    """Plugin that declares Greeting but only implements hello()."""

    def __init__(self):
        super().__init__(name="incomplete_greetings", provides={Greeting})

    async def hello(self, name="World"):
        return f"Hello, {name}!"

    # Missing goodbye() — should fail validation


class MathPlugin(Plugin):
    """Plugin that provides math via string (no protocol)."""

    def __init__(self):
        super().__init__(name="math", provides={"math"})


class MixedProviderPlugin(Plugin):
    """Plugin that provides both typed and string capabilities."""

    def __init__(self):
        super().__init__(name="mixed", provides={Greeting, "math"})

    async def hello(self, name="World"):
        return f"Hello, {name}!"

    async def goodbye(self, name="World"):
        return f"Goodbye, {name}!"


class TypedConsumerPlugin(Plugin):
    """Consumer that requires a typed capability."""

    def __init__(self):
        super().__init__(name="consumer", requires={Greeting})
        self.greet = None

    async def on_start(self):
        self.greet = await self.get_capability(Greeting)


class StringConsumerPlugin(Plugin):
    """Consumer that requires by string."""

    def __init__(self):
        super().__init__(name="string_consumer", requires={"greeting"})
        self.greet = None

    async def on_start(self):
        self.greet = await self.get_capability("greeting")


# ========== Name Derivation Tests ==========


class TestDeriveCapabilityName:
    """Tests for derive_capability_name utility."""

    def test_string_passthrough(self):
        assert derive_capability_name("greeting") == "greeting"

    def test_string_passthrough_snake_case(self):
        assert derive_capability_name("file_storage") == "file_storage"

    def test_simple_class(self):
        assert derive_capability_name(Greeting) == "greeting"

    def test_strip_capability_suffix(self):
        assert derive_capability_name(GreetingCapability) == "greeting"

    def test_strip_cap_suffix(self):
        assert derive_capability_name(MathCap) == "math"

    def test_multi_word_class(self):
        assert derive_capability_name(FileStorage) == "file_storage"

    def test_explicit_capability_name(self):
        assert derive_capability_name(CustomNamed) == "custom_name"

    def test_class_named_just_capability(self):
        """A class named exactly 'Capability' shouldn't strip to empty."""

        @runtime_checkable
        class Capability(Protocol):
            async def run(self) -> None: ...

        # "Capability" suffix is the entire name, so it shouldn't be stripped
        assert derive_capability_name(Capability) == "capability"

    def test_class_named_just_cap(self):
        """A class named exactly 'Cap' shouldn't strip to empty."""

        @runtime_checkable
        class Cap(Protocol):
            async def run(self) -> None: ...

        assert derive_capability_name(Cap) == "cap"


# ========== Normalize Capability Set Tests ==========


class TestNormalizeCapabilitySet:
    """Tests for normalize_capability_set utility."""

    def test_none_input(self):
        names, protocols = normalize_capability_set(None)
        assert names == frozenset()
        assert protocols == {}

    def test_empty_set(self):
        names, protocols = normalize_capability_set(set())
        assert names == frozenset()
        assert protocols == {}

    def test_strings_only(self):
        names, protocols = normalize_capability_set({"greeting", "math"})
        assert names == frozenset({"greeting", "math"})
        assert protocols == {}

    def test_types_only(self):
        names, protocols = normalize_capability_set({Greeting, FileStorage})
        assert names == frozenset({"greeting", "file_storage"})
        assert protocols == {"greeting": Greeting, "file_storage": FileStorage}

    def test_mixed_strings_and_types(self):
        names, protocols = normalize_capability_set({Greeting, "math"})
        assert names == frozenset({"greeting", "math"})
        assert protocols == {"greeting": Greeting}
        assert "math" not in protocols

    def test_frozenset_input(self):
        names, protocols = normalize_capability_set(frozenset({Greeting}))
        assert names == frozenset({"greeting"})
        assert protocols == {"greeting": Greeting}


# ========== Protocol Introspection Tests ==========


class TestGetProtocolMethods:
    """Tests for get_protocol_methods utility."""

    def test_extracts_methods(self):
        methods = get_protocol_methods(Greeting)
        method_names = [m["name"] for m in methods]
        assert "hello" in method_names
        assert "goodbye" in method_names

    def test_method_has_signature(self):
        methods = get_protocol_methods(Greeting)
        hello = next(m for m in methods if m["name"] == "hello")
        assert "signature" in hello
        assert "name" in hello["parameters"][0]

    def test_method_has_return_annotation(self):
        methods = get_protocol_methods(Greeting)
        hello = next(m for m in methods if m["name"] == "hello")
        assert hello["return_annotation"] == "str"

    def test_excludes_private_methods(self):
        methods = get_protocol_methods(Greeting)
        for m in methods:
            assert not m["name"].startswith("_")

    def test_parameter_details(self):
        methods = get_protocol_methods(Greeting)
        hello = next(m for m in methods if m["name"] == "hello")
        name_param = next(p for p in hello["parameters"] if p["name"] == "name")
        assert name_param["annotation"] == "str"
        assert "default" in name_param


# ========== Registration Validation Tests ==========


class TestTypedCapabilityRegistration:
    """Tests for registration-time protocol validation."""

    @pytest.mark.asyncio
    async def test_valid_provider_registers(self, started_core: Core):
        """Plugin implementing the protocol registers successfully."""
        result = await started_core.register_plugin(GreetingsPlugin())
        assert result is True

    @pytest.mark.asyncio
    async def test_incomplete_provider_fails(self, started_core: Core):
        """Plugin missing protocol methods fails at registration."""
        with pytest.raises(PluginError, match="does not implement.*Greeting.*protocol"):
            await started_core.register_plugin(IncompleteGreetingsPlugin())

    @pytest.mark.asyncio
    async def test_incomplete_provider_error_lists_missing_methods(self, started_core: Core):
        """Error message should list the missing methods."""
        with pytest.raises(PluginError, match="goodbye"):
            await started_core.register_plugin(IncompleteGreetingsPlugin())

    @pytest.mark.asyncio
    async def test_mixed_provides_registers(self, started_core: Core):
        """Plugin providing both typed and string capabilities registers."""
        result = await started_core.register_plugin(MixedProviderPlugin())
        assert result is True
        caps = await started_core._capability_system.list_capabilities()
        assert "greeting" in caps
        assert "math" in caps

    @pytest.mark.asyncio
    async def test_string_provides_still_works(self, started_core: Core):
        """Pure string-based provides still works as before."""
        result = await started_core.register_plugin(MathPlugin())
        assert result is True
        caps = await started_core._capability_system.list_capabilities()
        assert "math" in caps

    @pytest.mark.asyncio
    async def test_no_residual_state_on_validation_failure(self, started_core: Core):
        """Failed protocol validation should leave no state behind."""
        with pytest.raises(PluginError):
            await started_core.register_plugin(IncompleteGreetingsPlugin())

        all_plugins = await started_core._registry.all()
        assert all_plugins == {}
        assert await started_core._capability_system.list_capabilities() == []


# ========== Signature Compatibility Tests (Track K) ==========


class TestSignatureCompatibility:
    """Unit tests for the structural signature-compatibility rule."""

    def test_exact_match_compatible(self):
        def proto(self, path: str) -> bytes: ...
        def impl(self, path: str) -> bytes: ...

        assert signature_incompatibility(proto, impl) is None

    def test_provider_may_relax_required_to_optional(self):
        """A provider making a required protocol param optional is fine."""

        def proto(self, name): ...
        def impl(self, name="x"): ...

        assert signature_incompatibility(proto, impl) is None

    def test_var_kwargs_absorbs_named_params(self):
        """**kwargs satisfies any named protocol parameter (lenient case)."""

        def proto(self, a, b): ...
        def impl(self, **kw): ...

        assert signature_incompatibility(proto, impl) is None

    def test_var_kwargs_absorbs_keyword_only_param(self):
        """**kwargs satisfies a keyword-only protocol parameter."""

        def proto(self, *, a): ...
        def impl(self, **kw): ...

        assert signature_incompatibility(proto, impl) is None

    def test_var_args_absorbs_positional_param(self):
        """*args satisfies a positional protocol parameter."""

        def proto(self, a): ...
        def impl(self, *args): ...

        assert signature_incompatibility(proto, impl) is None

    def test_provider_kwonly_required_absorbed_by_protocol_var_kw(self):
        """A required keyword-only provider param is fine when the protocol has **kwargs."""

        def proto(self, **kw): ...
        def impl(self, *, a): ...

        assert signature_incompatibility(proto, impl) is None

    def test_provider_positional_required_absorbed_by_protocol_var_pos(self):
        """A required positional provider param is fine when the protocol has *args."""

        def proto(self, *args): ...
        def impl(self, a): ...

        assert signature_incompatibility(proto, impl) is None

    def test_extra_optional_param_ok(self):
        def proto(self, a): ...
        def impl(self, a, b=1): ...

        assert signature_incompatibility(proto, impl) is None

    def test_extra_required_param_incompatible(self):
        """A provider demanding a param the protocol never supplies is rejected."""

        def proto(self, a): ...
        def impl(self, a, b): ...

        assert signature_incompatibility(proto, impl) is not None

    def test_missing_protocol_param_incompatible(self):
        """A provider that cannot accept a declared param is rejected."""

        def proto(self, a, b): ...
        def impl(self, a): ...

        assert signature_incompatibility(proto, impl) is not None

    def test_return_annotation_mismatch_incompatible(self):
        def proto(self) -> int: ...
        def impl(self) -> str: ...

        assert signature_incompatibility(proto, impl) is not None

    def test_return_annotation_skipped_when_one_side_unannotated(self):
        def proto(self) -> int: ...
        def impl(self): ...

        assert signature_incompatibility(proto, impl) is None

    def test_unintrospectable_falls_back_to_presence(self):
        """A callable whose signature cannot be read does not raise."""
        assert signature_incompatibility(len, len) is None


class TestSignatureValidationThroughRegistration:
    """Integration: the signature rule fires through register_plugin."""

    @pytest.mark.asyncio
    async def test_incompatible_signature_rejected_at_registration(self, started_core: Core):
        """A method present but signature-incompatible is rejected (not just missing)."""

        class BadSigGreeter(Plugin):
            def __init__(self):
                super().__init__(name="bad_sig", provides={Greeting})

            async def hello(self, name="World"):
                return "hi"

            async def goodbye(self, name, extra):  # 'extra' not declared by protocol
                return "bye"

        with pytest.raises(PluginError, match="Incompatible methods.*goodbye"):
            await started_core.register_plugin(BadSigGreeter())

    @pytest.mark.asyncio
    async def test_kwargs_provider_is_compatible(self, started_core: Core):
        """A provider using **kwargs satisfies the contract (lenient path)."""

        class KwargsGreeter(Plugin):
            def __init__(self):
                super().__init__(name="kw_greeter", provides={Greeting})

            async def hello(self, **kwargs):
                return "hi"

            async def goodbye(self, **kwargs):
                return "bye"

        assert await started_core.register_plugin(KwargsGreeter()) is True


# ========== Resolution Tests ==========


class TestTypedCapabilityResolution:
    """Tests for resolution-time typed capability access."""

    @pytest.mark.asyncio
    async def test_get_capability_with_type(self, started_core: Core):
        """get_capability(Greeting) returns the provider."""
        provider = GreetingsPlugin()
        await started_core.register_plugin(provider)

        result = await started_core.get_capability(Greeting)
        assert result is provider

    @pytest.mark.asyncio
    async def test_get_capability_with_string(self, started_core: Core):
        """get_capability("greeting") still works alongside typed provider."""
        provider = GreetingsPlugin()
        await started_core.register_plugin(provider)

        result = await started_core.get_capability("greeting")
        assert result is provider

    @pytest.mark.asyncio
    async def test_plugin_get_capability_with_type(self, started_core: Core):
        """Plugin.get_capability(Greeting) convenience method works."""
        provider = GreetingsPlugin()
        consumer = TypedConsumerPlugin()

        await started_core.register_plugin(provider)
        await started_core.register_plugin(consumer)

        assert consumer.greet is provider

    @pytest.mark.asyncio
    async def test_string_consumer_with_typed_provider(self, started_core: Core):
        """String consumer can resolve a typed provider."""
        provider = GreetingsPlugin()
        consumer = StringConsumerPlugin()

        await started_core.register_plugin(provider)
        await started_core.register_plugin(consumer)

        assert consumer.greet is provider

    @pytest.mark.asyncio
    async def test_typed_consumer_with_string_provider_validates(self, started_core: Core):
        """Typed resolution validates even when provider used strings."""

        class StringProvider(Plugin):
            """Provides greeting by string, but actually implements the protocol."""

            def __init__(self):
                super().__init__(name="string_greeter", provides={"greeting"})

            async def hello(self, name="World"):
                return f"Hello, {name}!"

            async def goodbye(self, name="World"):
                return f"Goodbye, {name}!"

        provider = StringProvider()
        await started_core.register_plugin(provider)

        # Resolution with type validates the provider
        result = await started_core.get_capability(Greeting)
        assert result is provider

    @pytest.mark.asyncio
    async def test_typed_resolution_fails_for_non_conforming_string_provider(
        self, started_core: Core
    ):
        """Typed resolution catches a string provider that doesn't conform."""

        class BadProvider(Plugin):
            """Provides greeting by string but doesn't implement protocol."""

            def __init__(self):
                super().__init__(name="bad_greeter", provides={"greeting"})

            async def hello(self, name="World"):
                return f"Hello, {name}!"

            # Missing goodbye()

        await started_core.register_plugin(BadProvider())

        with pytest.raises(PluginError, match="does not implement.*Greeting.*protocol"):
            await started_core.get_capability(Greeting)

    @pytest.mark.asyncio
    async def test_missing_typed_capability_raises(self, started_core: Core):
        """get_capability(Greeting) raises CapabilityError when not available."""
        with pytest.raises(CapabilityError, match="greeting"):
            await started_core.get_capability(Greeting)


# ========== Capability Info Introspection Tests ==========


class TestTypedCapabilityInfo:
    """Tests for capability info introspection via snapshot_capability_info."""

    @pytest.mark.asyncio
    async def test_info_includes_typed_flag(self, started_core: Core):
        """Capability info should indicate when a protocol is associated."""
        await started_core.register_plugin(GreetingsPlugin())

        snapshot = started_core._capability_system.snapshot_capability_info()
        assert "greeting" in snapshot
        assert snapshot["greeting"].typed is True

    @pytest.mark.asyncio
    async def test_info_untyped_flag_for_string_only(self, started_core: Core):
        """String-only capability should have typed=False."""
        await started_core.register_plugin(MathPlugin())

        snapshot = started_core._capability_system.snapshot_capability_info()
        assert "math" in snapshot
        assert snapshot["math"].typed is False

    @pytest.mark.asyncio
    async def test_info_includes_protocol_methods(self, started_core: Core):
        """Capability info should include protocol method signatures."""
        await started_core.register_plugin(GreetingsPlugin())

        snapshot = started_core._capability_system.snapshot_capability_info()
        info = snapshot["greeting"]
        assert info.typed is True
        assert info.protocol_name == "Greeting"
        method_names = [m["name"] for m in info.protocol_methods]
        assert "hello" in method_names
        assert "goodbye" in method_names

    @pytest.mark.asyncio
    async def test_info_no_protocol_for_string_only(self, started_core: Core):
        """String-only capability should have empty protocol fields."""
        await started_core.register_plugin(MathPlugin())

        snapshot = started_core._capability_system.snapshot_capability_info()
        info = snapshot["math"]
        assert info.typed is False
        assert info.protocol_name == ""
        assert info.protocol_methods == []


# ========== Collision Policy Tests with Typed Capabilities ==========


class TestTypedCapabilityCollisions:
    """Tests for collision policies with typed capabilities."""

    @pytest.mark.asyncio
    async def test_error_on_conflict_with_typed(self):
        """Error on conflict policy works with typed capabilities."""
        core = Core(capability_collision="error_on_conflict")
        await core.start()

        class AnotherGreeter(Plugin):
            def __init__(self):
                super().__init__(name="another_greeter", provides={Greeting})

            async def hello(self, name="World"):
                return f"Hey {name}!"

            async def goodbye(self, name="World"):
                return f"Later {name}!"

        await core.register_plugin(GreetingsPlugin())
        with pytest.raises(PluginError):
            await core.register_plugin(AnotherGreeter())

    @pytest.mark.asyncio
    async def test_last_wins_with_typed(self):
        """Last wins policy works with typed capabilities."""
        core = Core(
            capability_collision="last_wins_with_warning",
            capability_selection="last_registered",
        )
        await core.start()

        class AnotherGreeter(Plugin):
            def __init__(self):
                super().__init__(name="another_greeter", provides={Greeting})

            async def hello(self, name="World"):
                return f"Hey {name}!"

            async def goodbye(self, name="World"):
                return f"Later {name}!"

        provider1 = GreetingsPlugin()
        provider2 = AnotherGreeter()

        await core.register_plugin(provider1)
        await core.register_plugin(provider2)

        result = await core.get_capability(Greeting)
        assert result is provider2


# ========== Cleanup Tests ==========


class TestTypedCapabilityCleanup:
    """Tests for cleanup of typed capability protocol mappings."""

    @pytest.mark.asyncio
    async def test_unregister_clears_protocol_type(self, started_core: Core):
        """Unregistering last provider clears the protocol type mapping."""
        provider = GreetingsPlugin()
        await started_core.register_plugin(provider)

        snapshot = started_core._capability_system.snapshot_capability_info()
        assert snapshot["greeting"].typed is True

        await started_core.unregister_plugin(provider.metadata.id)

        # Capability should be gone
        caps = await started_core._capability_system.list_capabilities()
        assert "greeting" not in caps

    @pytest.mark.asyncio
    async def test_drain_clears_protocol_types(self, started_core: Core):
        """Core stop drains protocol type mappings."""
        await started_core.register_plugin(GreetingsPlugin())
        await started_core.stop()

        caps = await started_core._capability_system.list_capabilities()
        assert caps == []


# ========== __capability_name__ Override Tests ==========


class TestCapabilityNameOverride:
    """Tests for the __capability_name__ override mechanism."""

    def test_override_takes_precedence(self):
        assert derive_capability_name(CustomNamed) == "custom_name"

    @pytest.mark.asyncio
    async def test_override_used_in_registration(self, started_core: Core):
        """Plugins using protocols with __capability_name__ register under that name."""

        @runtime_checkable
        class MyThing(Protocol):
            __capability_name__ = "special_service"

            async def run(self) -> None: ...

        class ThingPlugin(Plugin):
            def __init__(self):
                super().__init__(name="thing_plugin", provides={MyThing})

            async def run(self):
                pass

        await started_core.register_plugin(ThingPlugin())
        caps = await started_core._capability_system.list_capabilities()
        assert "special_service" in caps

        result = await started_core.get_capability("special_service")
        assert result is not None


# ========== PluginMetadata String Normalization Tests ==========


class TestMetadataStaysStringBased:
    """Verify that PluginMetadata always stores string-based capability names."""

    def test_provides_normalized_to_strings(self):
        core = Core()
        plugin = GreetingsPlugin()
        assert isinstance(plugin.metadata.provides, frozenset)
        assert all(isinstance(p, str) for p in plugin.metadata.provides)
        assert "greeting" in plugin.metadata.provides

    def test_requires_normalized_to_strings(self):
        core = Core()
        plugin = TypedConsumerPlugin()
        assert isinstance(plugin.metadata.requires, frozenset)
        assert all(isinstance(r, str) for r in plugin.metadata.requires)
        assert "greeting" in plugin.metadata.requires

    def test_mixed_provides_all_strings(self):
        core = Core()
        plugin = MixedProviderPlugin()
        assert all(isinstance(p, str) for p in plugin.metadata.provides)
        assert "greeting" in plugin.metadata.provides
        assert "math" in plugin.metadata.provides
