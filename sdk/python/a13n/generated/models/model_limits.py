from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ModelLimits")


@_attrs_define(repr=False)
class ModelLimits:
    """
    Attributes:
        context_window_tokens (int | None | Unset):
        max_output_tokens (int | None | Unset):
    """

    context_window_tokens: int | Unset | None = UNSET
    max_output_tokens: int | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        context_window_tokens: int | Unset | None
        if isinstance(self.context_window_tokens, Unset):
            context_window_tokens = UNSET
        else:
            context_window_tokens = self.context_window_tokens

        max_output_tokens: int | Unset | None
        if isinstance(self.max_output_tokens, Unset):
            max_output_tokens = UNSET
        else:
            max_output_tokens = self.max_output_tokens

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if context_window_tokens is not UNSET:
            field_dict["context_window_tokens"] = context_window_tokens
        if max_output_tokens is not UNSET:
            field_dict["max_output_tokens"] = max_output_tokens

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_context_window_tokens(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        context_window_tokens = _parse_context_window_tokens(d.pop("context_window_tokens", UNSET))

        def _parse_max_output_tokens(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        max_output_tokens = _parse_max_output_tokens(d.pop("max_output_tokens", UNSET))

        model_limits = cls(
            context_window_tokens=context_window_tokens,
            max_output_tokens=max_output_tokens,
        )

        return model_limits
