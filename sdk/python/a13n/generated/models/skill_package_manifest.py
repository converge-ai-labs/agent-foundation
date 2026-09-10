from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.skill_package_file import SkillPackageFile


T = TypeVar("T", bound="SkillPackageManifest")


@_attrs_define(repr=False)
class SkillPackageManifest:
    """
    Attributes:
        content_digest (str):
        description (str):
        files (list[SkillPackageFile]):
        skill_name (str):
        total_size_bytes (int):
        harness_skill_contract (Literal['1'] | Unset):
        schema_version (Literal['1'] | Unset):
    """

    content_digest: str
    description: str
    files: list[SkillPackageFile]
    skill_name: str
    total_size_bytes: int
    harness_skill_contract: Literal["1"] | Unset = UNSET
    schema_version: Literal["1"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        content_digest = self.content_digest

        description = self.description

        files = []
        for files_item_data in self.files:
            files_item = files_item_data.to_dict()
            files.append(files_item)

        skill_name = self.skill_name

        total_size_bytes = self.total_size_bytes

        harness_skill_contract = self.harness_skill_contract

        schema_version = self.schema_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "content_digest": content_digest,
                "description": description,
                "files": files,
                "skill_name": skill_name,
                "total_size_bytes": total_size_bytes,
            }
        )
        if harness_skill_contract is not UNSET:
            field_dict["harness_skill_contract"] = harness_skill_contract
        if schema_version is not UNSET:
            field_dict["schema_version"] = schema_version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.skill_package_file import SkillPackageFile

        d = dict(src_dict)
        content_digest = d.pop("content_digest")

        description = d.pop("description")

        files = []
        _files = d.pop("files")
        for files_item_data in _files:
            files_item = SkillPackageFile.from_dict(files_item_data)

            files.append(files_item)

        skill_name = d.pop("skill_name")

        total_size_bytes = d.pop("total_size_bytes")

        harness_skill_contract = cast(Literal["1"] | Unset, d.pop("harness_skill_contract", UNSET))
        if harness_skill_contract != "1" and not isinstance(harness_skill_contract, Unset):
            raise ValueError(f"harness_skill_contract must match const '1', got '{harness_skill_contract}'")

        schema_version = cast(Literal["1"] | Unset, d.pop("schema_version", UNSET))
        if schema_version != "1" and not isinstance(schema_version, Unset):
            raise ValueError(f"schema_version must match const '1', got '{schema_version}'")

        skill_package_manifest = cls(
            content_digest=content_digest,
            description=description,
            files=files,
            skill_name=skill_name,
            total_size_bytes=total_size_bytes,
            harness_skill_contract=harness_skill_contract,
            schema_version=schema_version,
        )

        return skill_package_manifest
