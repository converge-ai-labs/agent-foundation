from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ZipUploadSkillSource")


@_attrs_define(repr=False)
class ZipUploadSkillSource:
    """
    Attributes:
        upload_id (str):
        kind (Literal['zip_upload'] | Unset):
    """

    upload_id: str
    kind: Literal["zip_upload"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        upload_id = self.upload_id

        kind = self.kind

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "upload_id": upload_id,
            }
        )
        if kind is not UNSET:
            field_dict["kind"] = kind

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        upload_id = d.pop("upload_id")

        kind = cast(Literal["zip_upload"] | Unset, d.pop("kind", UNSET))
        if kind != "zip_upload" and not isinstance(kind, Unset):
            raise ValueError(f"kind must match const 'zip_upload', got '{kind}'")

        zip_upload_skill_source = cls(
            upload_id=upload_id,
            kind=kind,
        )

        return zip_upload_skill_source
