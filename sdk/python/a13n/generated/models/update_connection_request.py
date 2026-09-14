from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="UpdateConnectionRequest")


@_attrs_define(repr=False)
class UpdateConnectionRequest:
    """
    Attributes:
        expected_version (int):
        name (str):
    """

    expected_version: int
    name: str

    def to_dict(self) -> dict[str, Any]:
        expected_version = self.expected_version

        name = self.name

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
                "name": name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        name = d.pop("name")

        update_connection_request = cls(
            expected_version=expected_version,
            name=name,
        )

        return update_connection_request
