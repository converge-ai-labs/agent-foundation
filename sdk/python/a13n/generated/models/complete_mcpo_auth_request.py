from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="CompleteMCPOAuthRequest")


@_attrs_define(repr=False)
class CompleteMCPOAuthRequest:
    """
    Attributes:
        receipt (str):
        state (str):
    """

    receipt: str
    state: str

    def to_dict(self) -> dict[str, Any]:
        receipt = self.receipt

        state = self.state

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "receipt": receipt,
                "state": state,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        receipt = d.pop("receipt")

        state = d.pop("state")

        complete_mcpo_auth_request = cls(
            receipt=receipt,
            state=state,
        )

        return complete_mcpo_auth_request
