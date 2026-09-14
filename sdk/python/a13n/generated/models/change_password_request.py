from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="ChangePasswordRequest")


@_attrs_define(repr=False)
class ChangePasswordRequest:
    """
    Attributes:
        current_password (str):
        password (str):
    """

    current_password: str
    password: str

    def to_dict(self) -> dict[str, Any]:
        current_password = self.current_password

        password = self.password

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "current_password": current_password,
                "password": password,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        current_password = d.pop("current_password")

        password = d.pop("password")

        change_password_request = cls(
            current_password=current_password,
            password=password,
        )

        return change_password_request
