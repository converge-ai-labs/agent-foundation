from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="ConnectorSetupCompletion")


@_attrs_define(repr=False)
class ConnectorSetupCompletion:
    """
    Attributes:
        return_path (str):
    """

    return_path: str

    def to_dict(self) -> dict[str, Any]:
        return_path = self.return_path

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "return_path": return_path,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        return_path = d.pop("return_path")

        connector_setup_completion = cls(
            return_path=return_path,
        )

        return connector_setup_completion
