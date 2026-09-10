from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ProtocolLimits")


@_attrs_define(repr=False)
class ProtocolLimits:
    """
    Attributes:
        max_event_bytes (int | Unset):
        max_input_bytes (int | Unset):
        max_output_bytes (int | Unset):
    """

    max_event_bytes: int | Unset = UNSET
    max_input_bytes: int | Unset = UNSET
    max_output_bytes: int | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        max_event_bytes = self.max_event_bytes

        max_input_bytes = self.max_input_bytes

        max_output_bytes = self.max_output_bytes

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if max_event_bytes is not UNSET:
            field_dict["max_event_bytes"] = max_event_bytes
        if max_input_bytes is not UNSET:
            field_dict["max_input_bytes"] = max_input_bytes
        if max_output_bytes is not UNSET:
            field_dict["max_output_bytes"] = max_output_bytes

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        max_event_bytes = d.pop("max_event_bytes", UNSET)

        max_input_bytes = d.pop("max_input_bytes", UNSET)

        max_output_bytes = d.pop("max_output_bytes", UNSET)

        protocol_limits = cls(
            max_event_bytes=max_event_bytes,
            max_input_bytes=max_input_bytes,
            max_output_bytes=max_output_bytes,
        )

        return protocol_limits
