"""Public authoring descriptions of the plugins this Control process has loaded."""

from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from pydantic import BaseModel, JsonValue


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
        description = plugins.describe(registration.plugin_key)
        items.append(
            PluginType(
                plugin_key=registration.plugin_key,
                display_name=description.display_name or registration.plugin_key,
                description=description.description,
                config_schema=description.config_schema,
                distribution_name=registration.distribution_name,
                distribution_version=registration.distribution_version,
            )
        )
    return PluginCatalog(items=items)
