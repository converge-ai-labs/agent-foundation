from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RespondPendingResolution")


@_attrs_define(repr=False)
class RespondPendingResolution:
    """
    Attributes:
        call_id (str):
        response (Any):
        action (Literal['respond'] | Unset):
    """

    call_id: str
    response: Any
    action: Literal["respond"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        call_id = self.call_id

        response = self.response

        action = self.action

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "call_id": call_id,
                "response": response,
            }
        )
        if action is not UNSET:
            field_dict["action"] = action

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        call_id = d.pop("call_id")

        response = d.pop("response")

        action = cast(Literal["respond"] | Unset, d.pop("action", UNSET))
        if action != "respond" and not isinstance(action, Unset):
            raise ValueError(f"action must match const 'respond', got '{action}'")

        respond_pending_resolution = cls(
            call_id=call_id,
            response=response,
            action=action,
        )

        return respond_pending_resolution
