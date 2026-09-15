from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.toolset_selection_config import ToolsetSelectionConfig
    from ..models.toolset_selection_tools import ToolsetSelectionTools


T = TypeVar("T", bound="ToolsetSelection")


@_attrs_define(repr=False)
class ToolsetSelection:
    """
    Attributes:
        config (ToolsetSelectionConfig | Unset):
        enabled (bool | Unset):
        tools (ToolsetSelectionTools | Unset):
    """

    config: ToolsetSelectionConfig | Unset = UNSET
    enabled: bool | Unset = UNSET
    tools: ToolsetSelectionTools | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        enabled = self.enabled

        tools: dict[str, Any] | Unset = UNSET
        if not isinstance(self.tools, Unset):
            tools = self.tools.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if config is not UNSET:
            field_dict["config"] = config
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if tools is not UNSET:
            field_dict["tools"] = tools

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.toolset_selection_config import ToolsetSelectionConfig
        from ..models.toolset_selection_tools import ToolsetSelectionTools

        d = dict(src_dict)
        _config = d.pop("config", UNSET)
        config: ToolsetSelectionConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = ToolsetSelectionConfig.from_dict(_config)

        enabled = d.pop("enabled", UNSET)

        _tools = d.pop("tools", UNSET)
        tools: ToolsetSelectionTools | Unset
        if isinstance(_tools, Unset):
            tools = UNSET
        else:
            tools = ToolsetSelectionTools.from_dict(_tools)

        toolset_selection = cls(
            config=config,
            enabled=enabled,
            tools=tools,
        )

        return toolset_selection
