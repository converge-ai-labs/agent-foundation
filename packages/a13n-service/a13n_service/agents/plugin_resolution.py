"""Configuration validation against a process's immutable installed catalog."""

from a13n_harness import HarnessError
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog

from a13n_service.digests import digest_request

from .domain import PluginSelection


class PluginSelectionError(Exception):
    def __init__(self, reason: str, *, path: str = "plugins") -> None:
        super().__init__(reason)
        self.reason = reason
        self.path = path


def validate_plugin_selections(
    catalog: HarnessPluginFactoryCatalog,
    selections: tuple[PluginSelection, ...],
    *,
    retained: bool = False,
) -> tuple[PluginSelection, ...]:
    """Normalize new configuration; require frozen configuration to remain unchanged."""

    result = []
    for index, selection in enumerate(selections):
        path = f"plugins.{index}"
        try:
            config = dict(catalog.validate_configuration(selection.plugin_key, selection.config))
        except HarnessError as error:
            raise PluginSelectionError(error.code, path=path) from error
        if retained and digest_request(config) != digest_request(selection.config):
            raise PluginSelectionError("plugin_configuration_incompatible", path=path)
        result.append(
            PluginSelection(instance_name=selection.instance_name, plugin_key=selection.plugin_key, config=config)
        )
    return tuple(result)
