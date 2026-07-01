"""Tests for ConfigField and plugin config system."""

import pytest

from uxok import Core, Plugin
from uxok.errors import PluginError
from uxok.plugin import REQUIRED, ConfigField


def make_config_plugin(core, name="test", config_schema=None):
    """Build a Plugin instance with the given name and config schema.

    Single factory replacing the previous per-test plugin variants; named
    without a Test prefix so pytest never tries to collect it.
    """

    class ConfiguredPlugin(Plugin):
        def __init__(self):
            kwargs = {} if config_schema is None else {"config_schema": config_schema}
            super().__init__(name=name, **kwargs)

    return ConfiguredPlugin()


class TestPluginConfig:
    @pytest.mark.parametrize(
        ("core_kwargs", "config_schema", "raises_match", "expected"),
        [
            pytest.param(
                {"plugin_configs": {"test": {"db_url": "postgres://localhost"}}},
                {"db_url": ConfigField(str, REQUIRED)},
                None,
                {"db_url": "postgres://localhost"},
                id="reads-scoped-values",
            ),
            pytest.param(
                {"max_plugins": 100, "plugin_configs": {"test": {}}},
                None,
                None,
                {"max_plugins": 100},
                id="falls-back-to-core-config",
            ),
            pytest.param(
                {"plugin_configs": {"test": {}}},
                {"timeout": ConfigField(int, default=30)},
                None,
                {"timeout": 30},
                id="uses-schema-default",
            ),
            pytest.param(
                # Registration calls start(), so REQUIRED validation happens here.
                {"plugin_configs": {"test": {}}},
                {"db_url": ConfigField(str, REQUIRED, "Database URL")},
                "db_url.*required but not supplied",
                None,
                id="required-field-missing-raises",
            ),
            pytest.param(
                # Registration calls start(), so type validation happens here.
                {"plugin_configs": {"test": {"timeout": "not_an_int"}}},
                {"timeout": ConfigField(int, REQUIRED)},
                "timeout.*expected int",
                None,
                id="wrong-type-raises",
            ),
            pytest.param(
                {"plugin_configs": {"test": {"db_url": "postgres://localhost"}}},
                {"db_url": ConfigField(str, REQUIRED)},
                None,
                {"db_url": "postgres://localhost"},
                id="required-field-present-succeeds",
            ),
            pytest.param(
                {"max_plugins": 100},
                None,
                None,
                {"max_plugins": 100},
                id="no-schema-unchanged-behaviour",
            ),
            pytest.param(
                # Precedence: scoped value > schema default > CoreConfig fallback.
                {
                    "max_plugins": 100,
                    "plugin_configs": {
                        "test": {
                            "db_url": "postgres://localhost",
                            "timeout": 60,
                        }
                    },
                },
                {
                    "db_url": ConfigField(str, default="default_url"),
                    "timeout": ConfigField(int, default=30),
                    "retries": ConfigField(int, default=3),
                },
                None,
                {
                    "db_url": "postgres://localhost",
                    "timeout": 60,
                    "retries": 3,
                    "max_plugins": 100,
                },
                id="lookup-order-precedence",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_config_resolution(self, core_kwargs, config_schema, raises_match, expected):
        """plugin.config() resolution and validation across schema/config combinations."""
        core = Core(**core_kwargs)
        await core.start()
        plugin = make_config_plugin(core, config_schema=config_schema)

        if raises_match is not None:
            # register_plugin() calls start(), so schema validation happens here.
            with pytest.raises(PluginError, match=raises_match):
                await core.register_plugin(plugin)
            return

        await core.register_plugin(plugin)
        for key, value in expected.items():
            assert plugin.config(key) == value

    @pytest.mark.asyncio
    async def test_config_with_callable_default(self):
        """ConfigField can use callable defaults."""
        core = Core(plugin_configs={"test": {}})
        await core.start()
        plugin = make_config_plugin(core, config_schema={"items": ConfigField(list, default=[])})
        await core.register_plugin(plugin)

        # Each plugin gets its own list instance
        items = plugin.config("items")
        assert items == []
        items.append("test")
        assert plugin.config("items") == ["test"]

    @pytest.mark.asyncio
    async def test_config_multiple_plugins_isolated(self):
        """Each plugin has its own config namespace."""
        core = Core(
            plugin_configs={
                "plugin1": {"setting": "value1"},
                "plugin2": {"setting": "value2"},
            }
        )
        await core.start()

        plugin1 = make_config_plugin(
            core, name="plugin1", config_schema={"setting": ConfigField(str, REQUIRED)}
        )
        plugin2 = make_config_plugin(
            core, name="plugin2", config_schema={"setting": ConfigField(str, REQUIRED)}
        )

        await core.register_plugin(plugin1)
        await core.register_plugin(plugin2)

        assert plugin1.config("setting") == "value1"
        assert plugin2.config("setting") == "value2"
