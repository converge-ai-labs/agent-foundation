from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="MemoryAccess")


@_attrs_define(repr=False)
class MemoryAccess:
    """Current subject permissions; each content operation authorizes again.

    Attributes:
        can_write (bool):
    """

    can_write: bool

    def to_dict(self) -> dict[str, Any]:
        can_write = self.can_write

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "can_write": can_write,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        can_write = d.pop("can_write")

        memory_access = cls(
            can_write=can_write,
        )

        return memory_access
