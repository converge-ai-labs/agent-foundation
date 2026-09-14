from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="SkillSelection")


@_attrs_define(repr=False)
class SkillSelection:
    """
    Attributes:
        skill_key (str):
        version (int | None | Unset):
    """

    skill_key: str
    version: int | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        skill_key = self.skill_key

        version: int | Unset | None
        if isinstance(self.version, Unset):
            version = UNSET
        else:
            version = self.version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "skill_key": skill_key,
            }
        )
        if version is not UNSET:
            field_dict["version"] = version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        skill_key = d.pop("skill_key")

        def _parse_version(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        version = _parse_version(d.pop("version", UNSET))

        skill_selection = cls(
            skill_key=skill_key,
            version=version,
        )

        return skill_selection
