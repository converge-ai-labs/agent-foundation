from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="MemorySearch")


@_attrs_define(repr=False)
class MemorySearch:
    """
    Attributes:
        query (str):
        limit (int | Unset):
        threshold (float | None | Unset):
    """

    query: str
    limit: int | Unset = UNSET
    threshold: float | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        query = self.query

        limit = self.limit

        threshold: float | Unset | None
        if isinstance(self.threshold, Unset):
            threshold = UNSET
        else:
            threshold = self.threshold

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "query": query,
            }
        )
        if limit is not UNSET:
            field_dict["limit"] = limit
        if threshold is not UNSET:
            field_dict["threshold"] = threshold

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        query = d.pop("query")

        limit = d.pop("limit", UNSET)

        def _parse_threshold(data: object) -> float | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | Unset | None, data)

        threshold = _parse_threshold(d.pop("threshold", UNSET))

        memory_search = cls(
            query=query,
            limit=limit,
            threshold=threshold,
        )

        return memory_search
