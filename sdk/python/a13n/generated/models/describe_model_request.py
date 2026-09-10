from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="DescribeModelRequest")


@_attrs_define(repr=False)
class DescribeModelRequest:
    """
    Attributes:
        upstream_model (str):
        model_api (None | str | Unset):
    """

    upstream_model: str
    model_api: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        upstream_model = self.upstream_model

        model_api: str | Unset | None
        if isinstance(self.model_api, Unset):
            model_api = UNSET
        else:
            model_api = self.model_api

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "upstream_model": upstream_model,
            }
        )
        if model_api is not UNSET:
            field_dict["model_api"] = model_api

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        upstream_model = d.pop("upstream_model")

        def _parse_model_api(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        model_api = _parse_model_api(d.pop("model_api", UNSET))

        describe_model_request = cls(
            upstream_model=upstream_model,
            model_api=model_api,
        )

        return describe_model_request
