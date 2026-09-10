from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.error_detail_details import ErrorDetailDetails


T = TypeVar("T", bound="ErrorDetail")


@_attrs_define(repr=False)
class ErrorDetail:
    """
    Attributes:
        code (str):
        details (ErrorDetailDetails):
        message (str):
        request_id (str):
    """

    code: str
    details: ErrorDetailDetails
    message: str
    request_id: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        code = self.code

        details = self.details.to_dict()

        message = self.message

        request_id = self.request_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "code": code,
                "details": details,
                "message": message,
                "request_id": request_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.error_detail_details import ErrorDetailDetails

        d = dict(src_dict)
        code = d.pop("code")

        details = ErrorDetailDetails.from_dict(d.pop("details"))

        message = d.pop("message")

        request_id = d.pop("request_id")

        error_detail = cls(
            code=code,
            details=details,
            message=message,
            request_id=request_id,
        )

        error_detail.additional_properties = d
        return error_detail

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
