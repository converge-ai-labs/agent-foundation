from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="CompletePasswordResetRequest")


@_attrs_define(repr=False)
class CompletePasswordResetRequest:
    """
    Attributes:
        password (str):
        token (str):
    """

    password: str
    token: str

    def to_dict(self) -> dict[str, Any]:
        password = self.password

        token = self.token

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "password": password,
                "token": token,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        password = d.pop("password")

        token = d.pop("token")

        complete_password_reset_request = cls(
            password=password,
            token=token,
        )

        return complete_password_reset_request
