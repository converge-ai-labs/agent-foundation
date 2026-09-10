from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="CreateWorkspaceRequest")


@_attrs_define(repr=False)
class CreateWorkspaceRequest:
    """
    Attributes:
        name (str):
        key (None | str | Unset):
    """

    name: str
    key: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        key: str | Unset | None
        if isinstance(self.key, Unset):
            key = UNSET
        else:
            key = self.key

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "name": name,
            }
        )
        if key is not UNSET:
            field_dict["key"] = key

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        def _parse_key(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        key = _parse_key(d.pop("key", UNSET))

        create_workspace_request = cls(
            name=name,
            key=key,
        )

        return create_workspace_request
