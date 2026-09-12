from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="CompleteAuthorizationRequest")


@_attrs_define(repr=False)
class CompleteAuthorizationRequest:
    """
    Attributes:
        completion_verifier (str):
        receipt (str):
    """

    completion_verifier: str
    receipt: str

    def to_dict(self) -> dict[str, Any]:
        completion_verifier = self.completion_verifier

        receipt = self.receipt

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "completion_verifier": completion_verifier,
                "receipt": receipt,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        completion_verifier = d.pop("completion_verifier")

        receipt = d.pop("receipt")

        complete_authorization_request = cls(
            completion_verifier=completion_verifier,
            receipt=receipt,
        )

        return complete_authorization_request
