from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="ModelIdentity")


@_attrs_define(repr=False)
class ModelIdentity:
    """
    Attributes:
        requested (None | str):
        response (None | str):
    """

    requested: str | None
    response: str | None

    def to_dict(self) -> dict[str, Any]:
        requested: str | None
        requested = self.requested

        response: str | None
        response = self.response

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "requested": requested,
                "response": response,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_requested(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        requested = _parse_requested(d.pop("requested"))

        def _parse_response(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        response = _parse_response(d.pop("response"))

        model_identity = cls(
            requested=requested,
            response=response,
        )

        return model_identity
