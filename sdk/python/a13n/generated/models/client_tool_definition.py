from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.client_tool_definition_permission import ClientToolDefinitionPermission
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.client_tool_definition_metadata import ClientToolDefinitionMetadata
    from ..models.client_tool_definition_parameters_json_schema import ClientToolDefinitionParametersJsonSchema


T = TypeVar("T", bound="ClientToolDefinition")


@_attrs_define(repr=False)
class ClientToolDefinition:
    """Portable model guidance for one externally executed client tool.

    Attributes:
        description (str):
        name (str):
        parameters_json_schema (ClientToolDefinitionParametersJsonSchema):
        instruction (None | str | Unset):
        metadata (ClientToolDefinitionMetadata | Unset):
        permission (ClientToolDefinitionPermission | Unset):
    """

    description: str
    name: str
    parameters_json_schema: ClientToolDefinitionParametersJsonSchema
    instruction: str | Unset | None = UNSET
    metadata: ClientToolDefinitionMetadata | Unset = UNSET
    permission: ClientToolDefinitionPermission | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        name = self.name

        parameters_json_schema = self.parameters_json_schema.to_dict()

        instruction: str | Unset | None
        if isinstance(self.instruction, Unset):
            instruction = UNSET
        else:
            instruction = self.instruction

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        permission: str | Unset = UNSET
        if not isinstance(self.permission, Unset):
            permission = self.permission.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "description": description,
                "name": name,
                "parameters_json_schema": parameters_json_schema,
            }
        )
        if instruction is not UNSET:
            field_dict["instruction"] = instruction
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if permission is not UNSET:
            field_dict["permission"] = permission

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.client_tool_definition_metadata import ClientToolDefinitionMetadata
        from ..models.client_tool_definition_parameters_json_schema import (
            ClientToolDefinitionParametersJsonSchema,
        )

        d = dict(src_dict)
        description = d.pop("description")

        name = d.pop("name")

        parameters_json_schema = ClientToolDefinitionParametersJsonSchema.from_dict(d.pop("parameters_json_schema"))

        def _parse_instruction(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        instruction = _parse_instruction(d.pop("instruction", UNSET))

        _metadata = d.pop("metadata", UNSET)
        metadata: ClientToolDefinitionMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = ClientToolDefinitionMetadata.from_dict(_metadata)

        _permission = d.pop("permission", UNSET)
        permission: ClientToolDefinitionPermission | Unset
        if isinstance(_permission, Unset):
            permission = UNSET
        else:
            permission = ClientToolDefinitionPermission(_permission)

        client_tool_definition = cls(
            description=description,
            name=name,
            parameters_json_schema=parameters_json_schema,
            instruction=instruction,
            metadata=metadata,
            permission=permission,
        )

        return client_tool_definition
