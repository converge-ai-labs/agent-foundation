from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RetryOverride")


@_attrs_define(repr=False)
class RetryOverride:
    """
    Attributes:
        output (int | None | Unset):
        tools (int | None | Unset):
    """

    output: int | Unset | None = UNSET
    tools: int | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        output: int | Unset | None
        if isinstance(self.output, Unset):
            output = UNSET
        else:
            output = self.output

        tools: int | Unset | None
        if isinstance(self.tools, Unset):
            tools = UNSET
        else:
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

        def _parse_output(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        output = _parse_output(d.pop("output", UNSET))

        def _parse_tools(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        tools = _parse_tools(d.pop("tools", UNSET))

        retry_override = cls(
            output=output,
            tools=tools,
        )

        return retry_override
