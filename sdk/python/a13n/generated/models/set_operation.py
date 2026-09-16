from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="SetOperation")


@_attrs_define(repr=False)
class SetOperation:
    """
    Attributes:
        op (Literal['set']):
        path (list[str]):
        value (Any):
    """

    op: Literal["set"]
    path: list[str]
    value: Any

    def to_dict(self) -> dict[str, Any]:
        op = self.op

        path = self.path

        value = self.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "op": op,
                "path": path,
                "value": value,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        op = cast(Literal["set"], d.pop("op"))
        if op != "set":
            raise ValueError(f"op must match const 'set', got '{op}'")

        path = cast(list[str], d.pop("path"))

        value = d.pop("value")

        set_operation = cls(
            op=op,
            path=path,
            value=value,
        )

        return set_operation
