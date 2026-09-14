from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ApprovePendingResolution")


@_attrs_define(repr=False)
class ApprovePendingResolution:
    """
    Attributes:
        call_id (str):
        action (Literal['approve'] | Unset):
    """

    call_id: str
    action: Literal["approve"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        call_id = self.call_id

        action = self.action

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "call_id": call_id,
            }
        )
        if action is not UNSET:
            field_dict["action"] = action

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        call_id = d.pop("call_id")

        action = cast(Literal["approve"] | Unset, d.pop("action", UNSET))
        if action != "approve" and not isinstance(action, Unset):
            raise ValueError(f"action must match const 'approve', got '{action}'")

        approve_pending_resolution = cls(
            call_id=call_id,
            action=action,
        )

        return approve_pending_resolution
