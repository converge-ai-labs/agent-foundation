from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="AuthConfiguration")


@_attrs_define(repr=False)
class AuthConfiguration:
    """
    Attributes:
        email_delivery (bool):
    """

    email_delivery: bool

    def to_dict(self) -> dict[str, Any]:
        email_delivery = self.email_delivery

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "email_delivery": email_delivery,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        email_delivery = d.pop("email_delivery")

        auth_configuration = cls(
            email_delivery=email_delivery,
        )

        return auth_configuration
