from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.memory_scope import MemoryScope
from ..types import UNSET, Unset

T = TypeVar("T", bound="MemorySelection")


@_attrs_define(repr=False)
class MemorySelection:
    """Opt-in Agent behavior; backend credentials and subject IDs are host-owned.

    Attributes:
        provider_id (str):
        auto_recall (bool | Unset):
        recall_limit (int | Unset):
        recall_required (bool | Unset):
        recall_threshold (float | None | Unset):
        recall_timeout (float | Unset):
        scope (MemoryScope | None | Unset):
        toolset (bool | Unset):
    """

    provider_id: str
    auto_recall: bool | Unset = UNSET
    recall_limit: int | Unset = UNSET
    recall_required: bool | Unset = UNSET
    recall_threshold: float | Unset | None = UNSET
    recall_timeout: float | Unset = UNSET
    scope: MemoryScope | Unset | None = UNSET
    toolset: bool | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        provider_id = self.provider_id

        auto_recall = self.auto_recall

        recall_limit = self.recall_limit

        recall_required = self.recall_required

        recall_threshold: float | Unset | None
        if isinstance(self.recall_threshold, Unset):
            recall_threshold = UNSET
        else:
            recall_threshold = self.recall_threshold

        recall_timeout = self.recall_timeout

        scope: str | Unset | None
        if isinstance(self.scope, Unset):
            scope = UNSET
        elif isinstance(self.scope, MemoryScope):
            scope = self.scope.value
        else:
            scope = self.scope

        toolset = self.toolset

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "provider_id": provider_id,
            }
        )
        if auto_recall is not UNSET:
            field_dict["auto_recall"] = auto_recall
        if recall_limit is not UNSET:
            field_dict["recall_limit"] = recall_limit
        if recall_required is not UNSET:
            field_dict["recall_required"] = recall_required
        if recall_threshold is not UNSET:
            field_dict["recall_threshold"] = recall_threshold
        if recall_timeout is not UNSET:
            field_dict["recall_timeout"] = recall_timeout
        if scope is not UNSET:
            field_dict["scope"] = scope
        if toolset is not UNSET:
            field_dict["toolset"] = toolset

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        provider_id = d.pop("provider_id")

        auto_recall = d.pop("auto_recall", UNSET)

        recall_limit = d.pop("recall_limit", UNSET)

        recall_required = d.pop("recall_required", UNSET)

        def _parse_recall_threshold(data: object) -> float | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | Unset | None, data)

        recall_threshold = _parse_recall_threshold(d.pop("recall_threshold", UNSET))

        recall_timeout = d.pop("recall_timeout", UNSET)

        def _parse_scope(data: object) -> MemoryScope | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                scope_type_0 = MemoryScope(data)

                return scope_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(MemoryScope | Unset | None, data)

        scope = _parse_scope(d.pop("scope", UNSET))

        toolset = d.pop("toolset", UNSET)

        memory_selection = cls(
            provider_id=provider_id,
            auto_recall=auto_recall,
            recall_limit=recall_limit,
            recall_required=recall_required,
            recall_threshold=recall_threshold,
            recall_timeout=recall_timeout,
            scope=scope,
            toolset=toolset,
        )

        return memory_selection
