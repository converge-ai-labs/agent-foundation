from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="SharingParticipant")


@_attrs_define(repr=False)
class SharingParticipant:
    """
    Attributes:
        joined_at (datetime.datetime):
        scope_id (str):
    """

    joined_at: datetime.datetime
    scope_id: str

    def to_dict(self) -> dict[str, Any]:
        joined_at = self.joined_at.isoformat()

        scope_id = self.scope_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "joined_at": joined_at,
                "scope_id": scope_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        joined_at = datetime.datetime.fromisoformat(d.pop("joined_at"))

        scope_id = d.pop("scope_id")

        sharing_participant = cls(
            joined_at=joined_at,
            scope_id=scope_id,
        )

        return sharing_participant
