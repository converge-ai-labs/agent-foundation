from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="RemoveOperation")


@_attrs_define(repr=False)
class RemoveOperation:
    """
    Attributes:
        op (Literal['remove']):
        path (list[str]):
    """

    op: Literal["remove"]
    path: list[str]

    def to_dict(self) -> dict[str, Any]:
        op = self.op

        path = self.path

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "op": op,
                "path": path,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        op = cast(Literal["remove"], d.pop("op"))
        if op != "remove":
            raise ValueError(f"op must match const 'remove', got '{op}'")

        path = cast(list[str], d.pop("path"))

        remove_operation = cls(
            op=op,
            path=path,
        )

        return remove_operation
