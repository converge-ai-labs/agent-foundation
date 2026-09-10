from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="EmailChangeRequest")


@_attrs_define(repr=False)
class EmailChangeRequest:
    """
    Attributes:
        current_password (str):
        email (str):
    """

    current_password: str
    email: str

    def to_dict(self) -> dict[str, Any]:
        current_password = self.current_password

        email = self.email

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "current_password": current_password,
                "email": email,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        current_password = d.pop("current_password")

        email = d.pop("email")

        email_change_request = cls(
            current_password=current_password,
            email=email,
        )

        return email_change_request
