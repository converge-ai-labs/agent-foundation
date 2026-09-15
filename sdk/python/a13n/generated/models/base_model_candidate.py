from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="BaseModelCandidate")


@_attrs_define(repr=False)
class BaseModelCandidate:
    """
    Attributes:
        base_model (str):
        inferred_model_api (None | str):
        model_api_label (None | str):
    """

    base_model: str
    inferred_model_api: str | None
    model_api_label: str | None

    def to_dict(self) -> dict[str, Any]:
        base_model = self.base_model

        inferred_model_api: str | None
        inferred_model_api = self.inferred_model_api

        model_api_label: str | None
        model_api_label = self.model_api_label

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "base_model": base_model,
                "inferred_model_api": inferred_model_api,
                "model_api_label": model_api_label,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        base_model = d.pop("base_model")

        def _parse_inferred_model_api(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        inferred_model_api = _parse_inferred_model_api(d.pop("inferred_model_api"))

        def _parse_model_api_label(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        model_api_label = _parse_model_api_label(d.pop("model_api_label"))

        base_model_candidate = cls(
            base_model=base_model,
            inferred_model_api=inferred_model_api,
            model_api_label=model_api_label,
        )

        return base_model_candidate
