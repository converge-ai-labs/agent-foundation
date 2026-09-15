from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="AgentModelCharacteristics")


@_attrs_define(repr=False)
class AgentModelCharacteristics:
    """Agent-owned context policy layered over Model declarations.

    Attributes:
        compact_threshold (float | Unset):
        context_window_tokens (int | None | Unset):
        proactive_context_management_threshold (float | None | Unset):
    """

    compact_threshold: float | Unset = UNSET
    context_window_tokens: int | Unset | None = UNSET
    proactive_context_management_threshold: float | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        compact_threshold = self.compact_threshold

        context_window_tokens: int | Unset | None
        if isinstance(self.context_window_tokens, Unset):
            context_window_tokens = UNSET
        else:
            context_window_tokens = self.context_window_tokens

        proactive_context_management_threshold: float | Unset | None
        if isinstance(self.proactive_context_management_threshold, Unset):
            proactive_context_management_threshold = UNSET
        else:
            proactive_context_management_threshold = self.proactive_context_management_threshold

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if compact_threshold is not UNSET:
            field_dict["compact_threshold"] = compact_threshold
        if context_window_tokens is not UNSET:
            field_dict["context_window_tokens"] = context_window_tokens
        if proactive_context_management_threshold is not UNSET:
            field_dict["proactive_context_management_threshold"] = proactive_context_management_threshold

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        compact_threshold = d.pop("compact_threshold", UNSET)

        def _parse_context_window_tokens(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        context_window_tokens = _parse_context_window_tokens(d.pop("context_window_tokens", UNSET))

        def _parse_proactive_context_management_threshold(data: object) -> float | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | Unset | None, data)

        proactive_context_management_threshold = _parse_proactive_context_management_threshold(
            d.pop("proactive_context_management_threshold", UNSET)
        )

        agent_model_characteristics = cls(
            compact_threshold=compact_threshold,
            context_window_tokens=context_window_tokens,
            proactive_context_management_threshold=proactive_context_management_threshold,
        )

        return agent_model_characteristics
