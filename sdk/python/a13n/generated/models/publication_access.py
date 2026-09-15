from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="PublicationAccess")


@_attrs_define(repr=False)
class PublicationAccess:
    """
    Attributes:
        recipient_scope_ids (list[str]):
        version (int):
    """

    recipient_scope_ids: list[str]
    version: int

    def to_dict(self) -> dict[str, Any]:
        recipient_scope_ids = self.recipient_scope_ids

        version = self.version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "recipient_scope_ids": recipient_scope_ids,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        recipient_scope_ids = cast(list[str], d.pop("recipient_scope_ids"))

        version = d.pop("version")

        publication_access = cls(
            recipient_scope_ids=recipient_scope_ids,
            version=version,
        )

        return publication_access
