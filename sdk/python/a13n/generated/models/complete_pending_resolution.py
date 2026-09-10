from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="CompletePendingResolution")


@_attrs_define(repr=False)
class CompletePendingResolution:
    """
    Attributes:
        call_id (str):
        result (Any):
        action (Literal['complete'] | Unset):
    """

    call_id: str
    result: Any
    action: Literal["complete"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        call_id = self.call_id

        result = self.result

        action = self.action

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "call_id": call_id,
                "result": result,
            }
        )
        if action is not UNSET:
            field_dict["action"] = action

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        call_id = d.pop("call_id")

        result = d.pop("result")

        action = cast(Literal["complete"] | Unset, d.pop("action", UNSET))
        if action != "complete" and not isinstance(action, Unset):
            raise ValueError(f"action must match const 'complete', got '{action}'")

        complete_pending_resolution = cls(
            call_id=call_id,
            result=result,
            action=action,
        )

        return complete_pending_resolution
