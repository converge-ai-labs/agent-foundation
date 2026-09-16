from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="CreateBotTest")


@_attrs_define(repr=False)
class CreateBotTest:
    """
    Attributes:
        expected_version (int):
        target_id (str):
        target_version (int):
    """

    expected_version: int
    target_id: str
    target_version: int

    def to_dict(self) -> dict[str, Any]:
        expected_version = self.expected_version

        target_id = self.target_id

        target_version = self.target_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
                "target_id": target_id,
                "target_version": target_version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        target_id = d.pop("target_id")

        target_version = d.pop("target_version")

        create_bot_test = cls(
            expected_version=expected_version,
            target_id=target_id,
            target_version=target_version,
        )

        return create_bot_test
