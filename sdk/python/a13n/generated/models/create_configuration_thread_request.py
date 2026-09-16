from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="CreateConfigurationThreadRequest")


@_attrs_define(repr=False)
class CreateConfigurationThreadRequest:
    """
    Attributes:
        fork_from_run_id (str):
    """

    fork_from_run_id: str

    def to_dict(self) -> dict[str, Any]:
        fork_from_run_id = self.fork_from_run_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "fork_from_run_id": fork_from_run_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        fork_from_run_id = d.pop("fork_from_run_id")

        create_configuration_thread_request = cls(
            fork_from_run_id=fork_from_run_id,
        )

        return create_configuration_thread_request
