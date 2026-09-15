from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ModelCatalogSuggestionRequest")


@_attrs_define(repr=False)
class ModelCatalogSuggestionRequest:
    """
    Attributes:
        provider_id (str):
        upstream_model (str):
        base_model (None | str | Unset):
        model_api (None | str | Unset):
    """

    provider_id: str
    upstream_model: str
    base_model: str | Unset | None = UNSET
    model_api: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        provider_id = self.provider_id

        upstream_model = self.upstream_model

        base_model: str | Unset | None
        if isinstance(self.base_model, Unset):
            base_model = UNSET
        else:
            base_model = self.base_model

        model_api: str | Unset | None
        if isinstance(self.model_api, Unset):
            model_api = UNSET
        else:
            model_api = self.model_api

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "provider_id": provider_id,
                "upstream_model": upstream_model,
            }
        )
        if base_model is not UNSET:
            field_dict["base_model"] = base_model
        if model_api is not UNSET:
            field_dict["model_api"] = model_api

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        provider_id = d.pop("provider_id")

        upstream_model = d.pop("upstream_model")

        def _parse_base_model(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        base_model = _parse_base_model(d.pop("base_model", UNSET))

        def _parse_model_api(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        model_api = _parse_model_api(d.pop("model_api", UNSET))

        model_catalog_suggestion_request = cls(
            provider_id=provider_id,
            upstream_model=upstream_model,
            base_model=base_model,
            model_api=model_api,
        )

        return model_catalog_suggestion_request
