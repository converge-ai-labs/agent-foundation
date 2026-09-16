from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="PublicationAudience")


@_attrs_define(repr=False)
class PublicationAudience:
    """
    Attributes:
        expected_version (int):
        recipient_scope_ids (list[str]):
    """

    expected_version: int
    recipient_scope_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        expected_version = self.expected_version

        recipient_scope_ids = self.recipient_scope_ids

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
                "recipient_scope_ids": recipient_scope_ids,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        recipient_scope_ids = cast(list[str], d.pop("recipient_scope_ids"))

        publication_audience = cls(
            expected_version=expected_version,
            recipient_scope_ids=recipient_scope_ids,
        )

        return publication_audience
