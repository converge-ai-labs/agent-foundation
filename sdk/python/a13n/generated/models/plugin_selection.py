from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.plugin_selection_config import PluginSelectionConfig


T = TypeVar("T", bound="PluginSelection")


@_attrs_define(repr=False)
class PluginSelection:
    """
    Attributes:
        instance_name (str):
        plugin_key (str):
        config (PluginSelectionConfig | Unset):
    """

    instance_name: str
    plugin_key: str
    config: PluginSelectionConfig | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        instance_name = self.instance_name

        plugin_key = self.plugin_key

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "instance_name": instance_name,
                "plugin_key": plugin_key,
            }
        )
        if config is not UNSET:
            field_dict["config"] = config

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.plugin_selection_config import PluginSelectionConfig

        d = dict(src_dict)
        instance_name = d.pop("instance_name")

        plugin_key = d.pop("plugin_key")

        _config = d.pop("config", UNSET)
        config: PluginSelectionConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = PluginSelectionConfig.from_dict(_config)

        plugin_selection = cls(
            instance_name=instance_name,
            plugin_key=plugin_key,
            config=config,
        )

        return plugin_selection
