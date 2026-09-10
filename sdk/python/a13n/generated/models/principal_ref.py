from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.principal_type import PrincipalType

T = TypeVar("T", bound="PrincipalRef")


@_attrs_define(repr=False)
class PrincipalRef:
    """
    Attributes:
        principal_id (str):
        principal_type (PrincipalType):
    """

    principal_id: str
    principal_type: PrincipalType

    def to_dict(self) -> dict[str, Any]:
        principal_id = self.principal_id

        principal_type = self.principal_type.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "principal_id": principal_id,
                "principal_type": principal_type,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        principal_id = d.pop("principal_id")

        principal_type = PrincipalType(d.pop("principal_type"))

        principal_ref = cls(
            principal_id=principal_id,
            principal_type=principal_type,
        )

        return principal_ref
