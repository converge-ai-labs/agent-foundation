from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RetryConfig")


@_attrs_define(repr=False)
class RetryConfig:
    """
    Attributes:
        output (int | Unset):
        tools (int | Unset):
    """

    output: int | Unset = UNSET
    tools: int | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        output = self.output

        tools = self.tools

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if output is not UNSET:
            field_dict["output"] = output
        if tools is not UNSET:
            field_dict["tools"] = tools

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        output = d.pop("output", UNSET)

        tools = d.pop("tools", UNSET)

        retry_config = cls(
            output=output,
            tools=tools,
        )

        return retry_config
