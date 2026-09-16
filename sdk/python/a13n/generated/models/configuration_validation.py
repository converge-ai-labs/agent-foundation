from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ConfigurationValidation")


@_attrs_define(repr=False)
class ConfigurationValidation:
    """
    Attributes:
        checked_at (datetime.datetime):
        content_digest (str):
        dependency_digest (str):
        draft_version (int):
        warnings (list[str] | Unset):
    """

    checked_at: datetime.datetime
    content_digest: str
    dependency_digest: str
    draft_version: int
    warnings: list[str] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        checked_at = self.checked_at.isoformat()

        content_digest = self.content_digest

        dependency_digest = self.dependency_digest

        draft_version = self.draft_version

        warnings: list[str] | Unset = UNSET
        if not isinstance(self.warnings, Unset):
            warnings = self.warnings

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "checked_at": checked_at,
                "content_digest": content_digest,
                "dependency_digest": dependency_digest,
                "draft_version": draft_version,
            }
        )
        if warnings is not UNSET:
            field_dict["warnings"] = warnings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        checked_at = datetime.datetime.fromisoformat(d.pop("checked_at"))

        content_digest = d.pop("content_digest")

        dependency_digest = d.pop("dependency_digest")

        draft_version = d.pop("draft_version")

        warnings = cast(list[str], d.pop("warnings", UNSET))

        configuration_validation = cls(
            checked_at=checked_at,
            content_digest=content_digest,
            dependency_digest=dependency_digest,
            draft_version=draft_version,
            warnings=warnings,
        )

        return configuration_validation
