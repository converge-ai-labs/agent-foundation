from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.search_in import SearchIn

T = TypeVar("T", bound="TraceQueryDescriptor")


@_attrs_define(repr=False)
class TraceQueryDescriptor:
    """
    Attributes:
        enabled (bool):
        history_from (datetime.datetime | None):
        provider (str):
        search_in (list[SearchIn]):
    """

    enabled: bool
    history_from: datetime.datetime | None
    provider: str
    search_in: list[SearchIn]

    def to_dict(self) -> dict[str, Any]:
        enabled = self.enabled

        history_from: str | None
        if isinstance(self.history_from, datetime.datetime):
            history_from = self.history_from.isoformat()
        else:
            history_from = self.history_from

        provider = self.provider

        search_in = []
        for search_in_item_data in self.search_in:
            search_in_item = search_in_item_data.value
            search_in.append(search_in_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "enabled": enabled,
                "history_from": history_from,
                "provider": provider,
                "search_in": search_in,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        enabled = d.pop("enabled")

        def _parse_history_from(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                history_from_type_0 = datetime.datetime.fromisoformat(data)

                return history_from_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        history_from = _parse_history_from(d.pop("history_from"))

        provider = d.pop("provider")

        search_in = []
        _search_in = d.pop("search_in")
        for search_in_item_data in _search_in:
            search_in_item = SearchIn(search_in_item_data)

            search_in.append(search_in_item)

        trace_query_descriptor = cls(
            enabled=enabled,
            history_from=history_from,
            provider=provider,
            search_in=search_in,
        )

        return trace_query_descriptor
