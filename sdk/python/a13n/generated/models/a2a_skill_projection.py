from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="A2ASkillProjection")


@_attrs_define(repr=False)
class A2ASkillProjection:
    """
    Attributes:
        id (str):
        name (str):
        description (None | str | Unset):
    """

    id: str
    name: str
    description: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        name = self.name

        description: str | Unset | None
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "id": id,
                "name": name,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        name = d.pop("name")

        def _parse_description(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        description = _parse_description(d.pop("description", UNSET))

        a2a_skill_projection = cls(
            id=id,
            name=name,
            description=description,
        )

        return a2a_skill_projection
