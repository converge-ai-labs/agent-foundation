from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="AccountCommandRequest")


@_attrs_define(repr=False)
class AccountCommandRequest:
    """
    Attributes:
        expected_version (int):
    """

    expected_version: int

    def to_dict(self) -> dict[str, Any]:
        expected_version = self.expected_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        account_command_request = cls(
            expected_version=expected_version,
        )

        return account_command_request
