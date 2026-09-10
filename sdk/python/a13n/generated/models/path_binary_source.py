from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="PathBinarySource")


@_attrs_define(repr=False)
class PathBinarySource:
    """
    Attributes:
        environment_binding (str):
        path (str):
        type_ (Literal['path'] | Unset):
    """

    environment_binding: str
    path: str
    type_: Literal["path"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        environment_binding = self.environment_binding

        path = self.path

        type_ = self.type_

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "environment_binding": environment_binding,
                "path": path,
            }
        )
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        environment_binding = d.pop("environment_binding")

        path = d.pop("path")

        type_ = cast(Literal["path"] | Unset, d.pop("type", UNSET))
        if type_ != "path" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'path', got '{type_}'")

        path_binary_source = cls(
            environment_binding=environment_binding,
            path=path,
            type_=type_,
        )

        return path_binary_source
