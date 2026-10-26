"""Public authoring descriptions of the plugins this Control process has loaded."""

from a13n_harness.plugin_factories import HarnessPluginFactory, HarnessPluginFactoryCatalog
from a13n_logging import get_logger
from pydantic import BaseModel, JsonValue

logger = get_logger(__name__)


class PluginType(BaseModel):
    plugin_key: str
    display_name: str
    description: str
    config_schema: dict[str, JsonValue] | None
    distribution_name: str | None
    distribution_version: str | None


class PluginCatalog(BaseModel):
    items: list[PluginType]


def catalog(plugins: HarnessPluginFactoryCatalog) -> PluginCatalog:
    items = []
    for registration in sorted(plugins.registrations, key=lambda item: item.plugin_key):
        factory = plugins[registration.plugin_key]
        items.append(
            PluginType(
                plugin_key=registration.plugin_key,
                display_name=factory.display_name or registration.plugin_key,
                description=factory.description,
                config_schema=_configuration_schema(factory, registration.plugin_key),
                distribution_name=registration.distribution_name,
                distribution_version=registration.distribution_version,
            )
        )
    return PluginCatalog(items=items)


def _configuration_schema(factory: HarnessPluginFactory, plugin_key: str) -> dict[str, JsonValue] | None:
    if factory.configuration_model is None:
        return None
    try:
        return factory.configuration_model.model_json_schema(mode="validation")
    except Exception:
        # Custom Pydantic schema hooks must not break listing or expose their exception text.
        logger.warning("Plugin configuration schema is unavailable", extra={"plugin_key": plugin_key})
        return None
