from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.retention_window import RetentionWindow


T = TypeVar("T", bound="RetentionPolicy")


@_attrs_define(repr=False)
class RetentionPolicy:
    """
    Attributes:
        idle (RetentionWindow):
    """

    idle: RetentionWindow

    def to_dict(self) -> dict[str, Any]:
        idle = self.idle.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "idle": idle,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.retention_window import RetentionWindow

        d = dict(src_dict)
        idle = RetentionWindow.from_dict(d.pop("idle"))

        retention_policy = cls(
            idle=idle,
        )

        return retention_policy
