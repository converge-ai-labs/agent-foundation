from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="ExistingEnvironmentSelection")


@_attrs_define(repr=False)
class ExistingEnvironmentSelection:
    """
    Attributes:
        environment_id (str):
    """

    environment_id: str

    def to_dict(self) -> dict[str, Any]:
        environment_id = self.environment_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "environment_id": environment_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        environment_id = d.pop("environment_id")

        existing_environment_selection = cls(
            environment_id=environment_id,
        )

        return existing_environment_selection
