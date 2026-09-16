from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="ConfigurationDifference")


@_attrs_define(repr=False)
class ConfigurationDifference:
    """
    Attributes:
        after (Any):
        after_present (bool):
        before (Any):
        before_present (bool):
        path (list[str]):
    """

    after: Any
    after_present: bool
    before: Any
    before_present: bool
    path: list[str]

    def to_dict(self) -> dict[str, Any]:
        after = self.after

        after_present = self.after_present

        before = self.before

        before_present = self.before_present

        path = self.path

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "after": after,
                "after_present": after_present,
                "before": before,
                "before_present": before_present,
                "path": path,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        after = d.pop("after")

        after_present = d.pop("after_present")

        before = d.pop("before")

        before_present = d.pop("before_present")

        path = cast(list[str], d.pop("path"))

        configuration_difference = cls(
            after=after,
            after_present=after_present,
            before=before,
            before_present=before_present,
            path=path,
        )

        return configuration_difference
