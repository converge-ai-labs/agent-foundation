from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="InputBatchingPolicy")


@_attrs_define(repr=False)
class InputBatchingPolicy:
    """
    Attributes:
        max_batch_events (int):
        min_interval_ms (int):
    """

    max_batch_events: int
    min_interval_ms: int

    def to_dict(self) -> dict[str, Any]:
        max_batch_events = self.max_batch_events

        min_interval_ms = self.min_interval_ms

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "max_batch_events": max_batch_events,
                "min_interval_ms": min_interval_ms,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        max_batch_events = d.pop("max_batch_events")

        min_interval_ms = d.pop("min_interval_ms")

        input_batching_policy = cls(
            max_batch_events=max_batch_events,
            min_interval_ms=min_interval_ms,
        )

        return input_batching_policy
