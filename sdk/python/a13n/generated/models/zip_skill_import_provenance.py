from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ZipSkillImportProvenance")


@_attrs_define(repr=False)
class ZipSkillImportProvenance:
    """
    Attributes:
        archive_sha256 (str):
        kind (Literal['zip'] | Unset):
    """

    archive_sha256: str
    kind: Literal["zip"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        archive_sha256 = self.archive_sha256

        kind = self.kind

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "archive_sha256": archive_sha256,
            }
        )
        if kind is not UNSET:
            field_dict["kind"] = kind

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        archive_sha256 = d.pop("archive_sha256")

        kind = cast(Literal["zip"] | Unset, d.pop("kind", UNSET))
        if kind != "zip" and not isinstance(kind, Unset):
            raise ValueError(f"kind must match const 'zip', got '{kind}'")

        zip_skill_import_provenance = cls(
            archive_sha256=archive_sha256,
            kind=kind,
        )

        return zip_skill_import_provenance
