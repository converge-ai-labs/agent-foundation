from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="SearchSelection")


@_attrs_define(repr=False)
class SearchSelection:
    """
    Attributes:
        provider_id (str):
        allow_domains (list[str] | Unset):
        deny_domains (list[str] | Unset):
        include_domains (list[str] | Unset):
        max_results (int | Unset):
    """

    provider_id: str
    allow_domains: list[str] | Unset = UNSET
    deny_domains: list[str] | Unset = UNSET
    include_domains: list[str] | Unset = UNSET
    max_results: int | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        provider_id = self.provider_id

        allow_domains: list[str] | Unset = UNSET
        if not isinstance(self.allow_domains, Unset):
            allow_domains = self.allow_domains

        deny_domains: list[str] | Unset = UNSET
        if not isinstance(self.deny_domains, Unset):
            deny_domains = self.deny_domains

        include_domains: list[str] | Unset = UNSET
        if not isinstance(self.include_domains, Unset):
            include_domains = self.include_domains

        max_results = self.max_results

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "provider_id": provider_id,
            }
        )
        if allow_domains is not UNSET:
            field_dict["allow_domains"] = allow_domains
        if deny_domains is not UNSET:
            field_dict["deny_domains"] = deny_domains
        if include_domains is not UNSET:
            field_dict["include_domains"] = include_domains
        if max_results is not UNSET:
            field_dict["max_results"] = max_results

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        provider_id = d.pop("provider_id")

        allow_domains = cast(list[str], d.pop("allow_domains", UNSET))

        deny_domains = cast(list[str], d.pop("deny_domains", UNSET))

        include_domains = cast(list[str], d.pop("include_domains", UNSET))

        max_results = d.pop("max_results", UNSET)

        search_selection = cls(
            provider_id=provider_id,
            allow_domains=allow_domains,
            deny_domains=deny_domains,
            include_domains=include_domains,
            max_results=max_results,
        )

        return search_selection
