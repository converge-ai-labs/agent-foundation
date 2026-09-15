from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="MemoryPagination")


@_attrs_define(repr=False)
class MemoryPagination:
    """Native traversal; a null cursor means the final page of that traversal.

    Attributes:
        next_cursor (None | str):
    """

    next_cursor: str | None

    def to_dict(self) -> dict[str, Any]:
        next_cursor: str | None
        next_cursor = self.next_cursor

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "next_cursor": next_cursor,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_next_cursor(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        next_cursor = _parse_next_cursor(d.pop("next_cursor"))

        memory_pagination = cls(
            next_cursor=next_cursor,
        )

        return memory_pagination
