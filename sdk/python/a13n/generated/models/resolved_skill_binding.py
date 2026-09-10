from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ResolvedSkillBinding")


@_attrs_define(repr=False)
class ResolvedSkillBinding:
    """
    Attributes:
        skill_id (str):
        skill_key (str):
        version (int | None | Unset):
    """

    skill_id: str
    skill_key: str
    version: int | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        skill_id = self.skill_id

        skill_key = self.skill_key

        version: int | Unset | None
        if isinstance(self.version, Unset):
            version = UNSET
        else:
            version = self.version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "skill_id": skill_id,
                "skill_key": skill_key,
            }
        )
        if version is not UNSET:
            field_dict["version"] = version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        skill_id = d.pop("skill_id")

        skill_key = d.pop("skill_key")

        def _parse_version(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        version = _parse_version(d.pop("version", UNSET))

        resolved_skill_binding = cls(
            skill_id=skill_id,
            skill_key=skill_key,
            version=version,
        )

        return resolved_skill_binding
