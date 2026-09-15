from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..models.toolset_definition_key import ToolsetDefinitionKey

if TYPE_CHECKING:
    from ..models.tool_definition import ToolDefinition
    from ..models.toolset_definition_config_schema import ToolsetDefinitionConfigSchema


T = TypeVar("T", bound="ToolsetDefinition")


@_attrs_define(repr=False)
class ToolsetDefinition:
    """
    Attributes:
        config_schema (ToolsetDefinitionConfigSchema):
        default_enabled (bool):
        display_name (str):
        key (ToolsetDefinitionKey):
        tools (list[ToolDefinition]):
    """

    config_schema: ToolsetDefinitionConfigSchema
    default_enabled: bool
    display_name: str
    key: ToolsetDefinitionKey
    tools: list[ToolDefinition]

    def to_dict(self) -> dict[str, Any]:
        config_schema = self.config_schema.to_dict()

        default_enabled = self.default_enabled

        display_name = self.display_name

        key = self.key.value

        tools = []
        for tools_item_data in self.tools:
            tools_item = tools_item_data.to_dict()
            tools.append(tools_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "config_schema": config_schema,
                "default_enabled": default_enabled,
                "display_name": display_name,
                "key": key,
                "tools": tools,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.tool_definition import ToolDefinition
        from ..models.toolset_definition_config_schema import ToolsetDefinitionConfigSchema

        d = dict(src_dict)
        config_schema = ToolsetDefinitionConfigSchema.from_dict(d.pop("config_schema"))

        default_enabled = d.pop("default_enabled")

        display_name = d.pop("display_name")

        key = ToolsetDefinitionKey(d.pop("key"))

        tools = []
        _tools = d.pop("tools")
        for tools_item_data in _tools:
            tools_item = ToolDefinition.from_dict(tools_item_data)

            tools.append(tools_item)

        toolset_definition = cls(
            config_schema=config_schema,
            default_enabled=default_enabled,
            display_name=display_name,
            key=key,
            tools=tools,
        )

        return toolset_definition
