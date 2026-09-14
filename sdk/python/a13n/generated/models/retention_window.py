from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="RetentionWindow")


@_attrs_define(repr=False)
class RetentionWindow:
    """
    Attributes:
        delete_after (int | None):
        stop_after (int | None):
    """

    delete_after: int | None
    stop_after: int | None

    def to_dict(self) -> dict[str, Any]:
        delete_after: int | None
        delete_after = self.delete_after

        stop_after: int | None
        stop_after = self.stop_after

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "delete_after": delete_after,
                "stop_after": stop_after,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_delete_after(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        delete_after = _parse_delete_after(d.pop("delete_after"))

        def _parse_stop_after(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        stop_after = _parse_stop_after(d.pop("stop_after"))

        retention_window = cls(
            delete_after=delete_after,
            stop_after=stop_after,
        )

        return retention_window
