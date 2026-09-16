from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="SearchDocuments")


@_attrs_define(repr=False)
class SearchDocuments:
    """
    Attributes:
        query (str):
        include_shared (bool | Unset):
        limit (int | Unset):
    """

    query: str
    include_shared: bool | Unset = UNSET
    limit: int | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        query = self.query

        include_shared = self.include_shared

        limit = self.limit

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "query": query,
            }
        )
        if include_shared is not UNSET:
            field_dict["include_shared"] = include_shared
        if limit is not UNSET:
            field_dict["limit"] = limit

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        query = d.pop("query")

        include_shared = d.pop("include_shared", UNSET)

        limit = d.pop("limit", UNSET)

        search_documents = cls(
            query=query,
            include_shared=include_shared,
            limit=limit,
        )

        return search_documents
