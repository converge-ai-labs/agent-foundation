from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.output_variant_resources import OutputVariantResources
    from ..models.output_variant_schema import OutputVariantSchema


T = TypeVar("T", bound="OutputVariant")


@_attrs_define(repr=False)
class OutputVariant:
    """
    Attributes:
        name (str):
        schema (OutputVariantSchema):
        description (None | str | Unset):
        resources (OutputVariantResources | Unset):
    """

    name: str
    schema: OutputVariantSchema
    description: str | Unset | None = UNSET
    resources: OutputVariantResources | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        schema = self.schema.to_dict()

        description: str | Unset | None
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        resources: dict[str, Any] | Unset = UNSET
        if not isinstance(self.resources, Unset):
            resources = self.resources.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "name": name,
                "schema": schema,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if resources is not UNSET:
            field_dict["resources"] = resources

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.output_variant_resources import OutputVariantResources
        from ..models.output_variant_schema import OutputVariantSchema

        d = dict(src_dict)
        name = d.pop("name")

        schema = OutputVariantSchema.from_dict(d.pop("schema"))

        def _parse_description(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        description = _parse_description(d.pop("description", UNSET))

        _resources = d.pop("resources", UNSET)
        resources: OutputVariantResources | Unset
        if isinstance(_resources, Unset):
            resources = UNSET
        else:
            resources = OutputVariantResources.from_dict(_resources)

        output_variant = cls(
            name=name,
            schema=schema,
            description=description,
            resources=resources,
        )

        return output_variant
