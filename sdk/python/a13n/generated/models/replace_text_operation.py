from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="ReplaceTextOperation")


@_attrs_define(repr=False)
class ReplaceTextOperation:
    """
    Attributes:
        new_text (str):
        old_text (str):
        op (Literal['replace_text']):
        path (list[str]):
    """

    new_text: str
    old_text: str
    op: Literal["replace_text"]
    path: list[str]

    def to_dict(self) -> dict[str, Any]:
        new_text = self.new_text

        old_text = self.old_text

        op = self.op

        path = self.path

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "new_text": new_text,
                "old_text": old_text,
                "op": op,
                "path": path,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        new_text = d.pop("new_text")

        old_text = d.pop("old_text")

        op = cast(Literal["replace_text"], d.pop("op"))
        if op != "replace_text":
            raise ValueError(f"op must match const 'replace_text', got '{op}'")

        path = cast(list[str], d.pop("path"))

        replace_text_operation = cls(
            new_text=new_text,
            old_text=old_text,
            op=op,
            path=path,
        )

        return replace_text_operation
