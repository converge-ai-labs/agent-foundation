"""Editor schemas come from the validation model without affecting plugin startup."""

from collections.abc import Callable

from a13n_harness.plugin_factories import HarnessPluginFactory, build_harness_plugin_factory_catalog
from a13n_service.resources.agents.plugins import catalog
from pydantic import BaseModel, Field, JsonValue, RootModel


class Settings(BaseModel):
    count: int = Field(default=1, ge=1, title="Record count", description="Number of records.")


class Factory(HarnessPluginFactory):
    configuration_model = Settings
    display_name = "Recorder"
    description = "Record run events."

    @classmethod
    def plugin_key(cls):
        return "test.recorder"

    def create_plugin(self, context):
        raise AssertionError("listing must not construct plugins")


def test_catalog_derives_schema_without_validating_or_constructing_plugins():
    class ListingOnly(Factory):
        def validate_configuration(self, configuration):
            raise AssertionError("listing must not validate configuration")

    factories = build_harness_plugin_factory_catalog(explicit_factories=[ListingOnly()])
    item = catalog(factories).items[0]
    assert item.display_name == "Recorder"
    assert item.description == "Record run events."
    assert item.config_schema == Settings.model_json_schema(mode="validation")
    assert item.config_schema["properties"]["count"]["default"] == 1
    item.config_schema.clear()
    assert catalog(factories).items[0].config_schema == Settings.model_json_schema()


def test_legacy_factory_keeps_json_editing_and_its_validator():
    class Legacy(Factory):
        configuration_model = None
        display_name = ""

        def validate_configuration(self, configuration):
            return RootModel[dict[str, JsonValue]](dict(configuration))

    factories = build_harness_plugin_factory_catalog(explicit_factories=[Legacy()])
    item = catalog(factories).items[0]
    assert item.display_name == "test.recorder"
    assert item.config_schema is None
    assert factories.validate_configuration(item.plugin_key, {"legacy": True}) == {"legacy": True}


def test_unrepresentable_schema_does_not_block_catalog_or_backend_validation():
    class WithCallback(BaseModel):
        callback: Callable[[], None] = Field(default=lambda: None, exclude=True)
        count: int = 1

    class Unrepresentable(Factory):
        configuration_model = WithCallback

    # Loading succeeds without trying to generate a schema; the editor falls back to JSON.
    factories = build_harness_plugin_factory_catalog(explicit_factories=[Unrepresentable()])
    assert catalog(factories).items[0].config_schema is None
    assert factories.validate_configuration("test.recorder", {"count": 2}) == {"count": 2}


def test_schema_hook_failure_is_isolated_and_does_not_leak_details(caplog):
    class BrokenSchema(Settings):
        @classmethod
        def model_json_schema(cls, **kwargs):
            raise RuntimeError("secret /private/plugin.py")

    class Broken(Factory):
        configuration_model = BrokenSchema

        @classmethod
        def plugin_key(cls):
            return "test.broken"

    factories = build_harness_plugin_factory_catalog(explicit_factories=[Factory(), Broken()])
    items = catalog(factories).items
    assert [(item.plugin_key, item.config_schema is None) for item in items] == [
        ("test.broken", True),
        ("test.recorder", False),
    ]
    assert factories.validate_configuration("test.broken", {}) == {"count": 1}
    assert "schema is unavailable" in caplog.text
    assert "secret" not in caplog.text
    assert "/private" not in caplog.text
