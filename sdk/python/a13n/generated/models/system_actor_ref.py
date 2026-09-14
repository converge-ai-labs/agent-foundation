from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="SystemActorRef")


@_attrs_define(repr=False)
class SystemActorRef:
    """Historical system attribution; never an authenticatable Principal.

    Attributes:
        principal_id (str):
        principal_type (Literal['system'] | Unset):
    """

    principal_id: str
    principal_type: Literal["system"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        principal_id = self.principal_id

        principal_type = self.principal_type

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "principal_id": principal_id,
            }
        )
        if principal_type is not UNSET:
            field_dict["principal_type"] = principal_type

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        principal_id = d.pop("principal_id")

        principal_type = cast(Literal["system"] | Unset, d.pop("principal_type", UNSET))
        if principal_type != "system" and not isinstance(principal_type, Unset):
            raise ValueError(f"principal_type must match const 'system', got '{principal_type}'")

        system_actor_ref = cls(
            principal_id=principal_id,
            principal_type=principal_type,
        )

        return system_actor_ref
