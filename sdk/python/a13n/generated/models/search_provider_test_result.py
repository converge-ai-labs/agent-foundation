from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="SearchProviderTestResult")


@_attrs_define(repr=False)
class SearchProviderTestResult:
    """
    Attributes:
        checked_at (datetime.datetime):
        code (None | str):
        success (bool):
    """

    checked_at: datetime.datetime
    code: str | None
    success: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        checked_at = self.checked_at.isoformat()

        code: str | None
        code = self.code

        success = self.success

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "checked_at": checked_at,
                "code": code,
                "success": success,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        checked_at = datetime.datetime.fromisoformat(d.pop("checked_at"))

        def _parse_code(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        code = _parse_code(d.pop("code"))

        success = d.pop("success")

        search_provider_test_result = cls(
            checked_at=checked_at,
            code=code,
            success=success,
        )

        search_provider_test_result.additional_properties = d
        return search_provider_test_result

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
