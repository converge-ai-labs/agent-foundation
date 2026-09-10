from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.mcp_tool_annotations import MCPToolAnnotations
    from ..models.mcp_tool_input_schema import MCPToolInputSchema
    from ..models.mcp_tool_output_schema_type_0 import MCPToolOutputSchemaType0


T = TypeVar("T", bound="MCPTool")


@_attrs_define(repr=False)
class MCPTool:
    """
    Attributes:
        input_schema (MCPToolInputSchema):
        name (str):
        annotations (MCPToolAnnotations | Unset):
        description (str | Unset):
        output_schema (MCPToolOutputSchemaType0 | None | Unset):
    """

    input_schema: MCPToolInputSchema
    name: str
    annotations: MCPToolAnnotations | Unset = UNSET
    description: str | Unset = UNSET
    output_schema: MCPToolOutputSchemaType0 | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.mcp_tool_output_schema_type_0 import MCPToolOutputSchemaType0

        input_schema = self.input_schema.to_dict()

        name = self.name

        annotations: dict[str, Any] | Unset = UNSET
        if not isinstance(self.annotations, Unset):
            annotations = self.annotations.to_dict()

        description = self.description

        output_schema: dict[str, Any] | Unset | None
        if isinstance(self.output_schema, Unset):
            output_schema = UNSET
        elif isinstance(self.output_schema, MCPToolOutputSchemaType0):
            output_schema = self.output_schema.to_dict()
        else:
            output_schema = self.output_schema

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "input_schema": input_schema,
                "name": name,
            }
        )
        if annotations is not UNSET:
            field_dict["annotations"] = annotations
        if description is not UNSET:
            field_dict["description"] = description
        if output_schema is not UNSET:
            field_dict["output_schema"] = output_schema

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.mcp_tool_annotations import MCPToolAnnotations
        from ..models.mcp_tool_input_schema import MCPToolInputSchema
        from ..models.mcp_tool_output_schema_type_0 import MCPToolOutputSchemaType0

        d = dict(src_dict)
        input_schema = MCPToolInputSchema.from_dict(d.pop("input_schema"))

        name = d.pop("name")

        _annotations = d.pop("annotations", UNSET)
        annotations: MCPToolAnnotations | Unset
        if isinstance(_annotations, Unset):
            annotations = UNSET
        else:
            annotations = MCPToolAnnotations.from_dict(_annotations)

        description = d.pop("description", UNSET)

        def _parse_output_schema(data: object) -> MCPToolOutputSchemaType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                output_schema_type_0 = MCPToolOutputSchemaType0.from_dict(data)

                return output_schema_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(MCPToolOutputSchemaType0 | Unset | None, data)

        output_schema = _parse_output_schema(d.pop("output_schema", UNSET))

        mcp_tool = cls(
            input_schema=input_schema,
            name=name,
            annotations=annotations,
            description=description,
            output_schema=output_schema,
        )

        return mcp_tool
