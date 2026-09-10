from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UsageLimitsInput")


@_attrs_define(repr=False)
class UsageLimitsInput:
    """Limits on model usage.

    The request count is tracked by pydantic_ai, and the request limit is checked before each request to the model.
    Token counts are provided in responses from the model, and the token limits are checked after each response.

    Each of the limits can be set to `None` to disable that limit.

        Attributes:
            cost_limit (float | None | str | Unset):
            count_tokens_before_request (bool | Unset):
            input_tokens_limit (int | None | Unset):
            output_tokens_limit (int | None | Unset):
            per_request_input_tokens_limit (int | None | Unset):
            request_limit (int | None | Unset):
            tool_calls_limit (int | None | Unset):
            total_tokens_limit (int | None | Unset):
    """

    cost_limit: float | str | Unset | None = UNSET
    count_tokens_before_request: bool | Unset = UNSET
    input_tokens_limit: int | Unset | None = UNSET
    output_tokens_limit: int | Unset | None = UNSET
    per_request_input_tokens_limit: int | Unset | None = UNSET
    request_limit: int | Unset | None = UNSET
    tool_calls_limit: int | Unset | None = UNSET
    total_tokens_limit: int | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cost_limit: float | str | Unset | None
        if isinstance(self.cost_limit, Unset):
            cost_limit = UNSET
        else:
            cost_limit = self.cost_limit

        count_tokens_before_request = self.count_tokens_before_request

        input_tokens_limit: int | Unset | None
        if isinstance(self.input_tokens_limit, Unset):
            input_tokens_limit = UNSET
        else:
            input_tokens_limit = self.input_tokens_limit

        output_tokens_limit: int | Unset | None
        if isinstance(self.output_tokens_limit, Unset):
            output_tokens_limit = UNSET
        else:
            output_tokens_limit = self.output_tokens_limit

        per_request_input_tokens_limit: int | Unset | None
        if isinstance(self.per_request_input_tokens_limit, Unset):
            per_request_input_tokens_limit = UNSET
        else:
            per_request_input_tokens_limit = self.per_request_input_tokens_limit

        request_limit: int | Unset | None
        if isinstance(self.request_limit, Unset):
            request_limit = UNSET
        else:
            request_limit = self.request_limit

        tool_calls_limit: int | Unset | None
        if isinstance(self.tool_calls_limit, Unset):
            tool_calls_limit = UNSET
        else:
            tool_calls_limit = self.tool_calls_limit

        total_tokens_limit: int | Unset | None
        if isinstance(self.total_tokens_limit, Unset):
            total_tokens_limit = UNSET
        else:
            total_tokens_limit = self.total_tokens_limit

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if cost_limit is not UNSET:
            field_dict["cost_limit"] = cost_limit
        if count_tokens_before_request is not UNSET:
            field_dict["count_tokens_before_request"] = count_tokens_before_request
        if input_tokens_limit is not UNSET:
            field_dict["input_tokens_limit"] = input_tokens_limit
        if output_tokens_limit is not UNSET:
            field_dict["output_tokens_limit"] = output_tokens_limit
        if per_request_input_tokens_limit is not UNSET:
            field_dict["per_request_input_tokens_limit"] = per_request_input_tokens_limit
        if request_limit is not UNSET:
            field_dict["request_limit"] = request_limit
        if tool_calls_limit is not UNSET:
            field_dict["tool_calls_limit"] = tool_calls_limit
        if total_tokens_limit is not UNSET:
            field_dict["total_tokens_limit"] = total_tokens_limit

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_cost_limit(data: object) -> float | str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | str | Unset | None, data)

        cost_limit = _parse_cost_limit(d.pop("cost_limit", UNSET))

        count_tokens_before_request = d.pop("count_tokens_before_request", UNSET)

        def _parse_input_tokens_limit(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        input_tokens_limit = _parse_input_tokens_limit(d.pop("input_tokens_limit", UNSET))

        def _parse_output_tokens_limit(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        output_tokens_limit = _parse_output_tokens_limit(d.pop("output_tokens_limit", UNSET))

        def _parse_per_request_input_tokens_limit(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        per_request_input_tokens_limit = _parse_per_request_input_tokens_limit(
            d.pop("per_request_input_tokens_limit", UNSET)
        )

        def _parse_request_limit(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        request_limit = _parse_request_limit(d.pop("request_limit", UNSET))

        def _parse_tool_calls_limit(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        tool_calls_limit = _parse_tool_calls_limit(d.pop("tool_calls_limit", UNSET))

        def _parse_total_tokens_limit(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        total_tokens_limit = _parse_total_tokens_limit(d.pop("total_tokens_limit", UNSET))

        usage_limits_input = cls(
            cost_limit=cost_limit,
            count_tokens_before_request=count_tokens_before_request,
            input_tokens_limit=input_tokens_limit,
            output_tokens_limit=output_tokens_limit,
            per_request_input_tokens_limit=per_request_input_tokens_limit,
            request_limit=request_limit,
            tool_calls_limit=tool_calls_limit,
            total_tokens_limit=total_tokens_limit,
        )

        usage_limits_input.additional_properties = d
        return usage_limits_input

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
