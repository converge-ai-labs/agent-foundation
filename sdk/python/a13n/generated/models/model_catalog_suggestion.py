from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.model_declarations import ModelDeclarations


T = TypeVar("T", bound="ModelCatalogSuggestion")


@_attrs_define(repr=False)
class ModelCatalogSuggestion:
    """
    Attributes:
        base_model (str):
        declarations (ModelDeclarations): Harness-facing facts and authoring choices declared for one saved Model.
        model_api (None | str):
        model_api_label (None | str):
    """

    base_model: str
    declarations: ModelDeclarations
    model_api: str | None
    model_api_label: str | None

    def to_dict(self) -> dict[str, Any]:
        base_model = self.base_model

        declarations = self.declarations.to_dict()

        model_api: str | None
        model_api = self.model_api

        model_api_label: str | None
        model_api_label = self.model_api_label

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "base_model": base_model,
                "declarations": declarations,
                "model_api": model_api,
                "model_api_label": model_api_label,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.model_declarations import ModelDeclarations

        d = dict(src_dict)
        base_model = d.pop("base_model")

        declarations = ModelDeclarations.from_dict(d.pop("declarations"))

        def _parse_model_api(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        model_api = _parse_model_api(d.pop("model_api"))

        def _parse_model_api_label(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        model_api_label = _parse_model_api_label(d.pop("model_api_label"))

        model_catalog_suggestion = cls(
            base_model=base_model,
            declarations=declarations,
            model_api=model_api,
            model_api_label=model_api_label,
        )

        return model_catalog_suggestion
