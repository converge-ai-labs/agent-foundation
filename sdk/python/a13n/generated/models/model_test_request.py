from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="ModelTestRequest")


@_attrs_define(repr=False)
class ModelTestRequest:
    """Model tests use the saved API and settings without a request selector."""

    def to_dict(self) -> dict[str, Any]:

        field_dict: dict[str, Any] = {}

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        model_test_request = cls()

        return model_test_request
