from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="SkillPackageFile")


@_attrs_define(repr=False)
class SkillPackageFile:
    """
    Attributes:
        path (str):
        sha256 (str):
        size_bytes (int):
    """

    path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        path = self.path

        sha256 = self.sha256

        size_bytes = self.size_bytes

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "path": path,
                "sha256": sha256,
                "size_bytes": size_bytes,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        path = d.pop("path")

        sha256 = d.pop("sha256")

        size_bytes = d.pop("size_bytes")

        skill_package_file = cls(
            path=path,
            sha256=sha256,
            size_bytes=size_bytes,
        )

        return skill_package_file
