from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="UpdateConnectorProviderRequest")


@_attrs_define(repr=False)
class UpdateConnectorProviderRequest:
    """
    Attributes:
        expected_version (int):
        name (None | str | Unset):
    """

    expected_version: int
    name: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        expected_version = self.expected_version

        name: str | Unset | None
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
            }
        )
        if name is not UNSET:
            field_dict["name"] = name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        def _parse_name(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        name = _parse_name(d.pop("name", UNSET))

        update_connector_provider_request = cls(
            expected_version=expected_version,
            name=name,
        )

        return update_connector_provider_request
