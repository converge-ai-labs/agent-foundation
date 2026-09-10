from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="EnvironmentState")


@_attrs_define(repr=False)
class EnvironmentState:
    """Provider-owned portable semantic soft reference for one target.

    Attributes:
        provider_key (str):
        state (Any):
        state_version (str):
    """

    provider_key: str
    state: Any
    state_version: str

    def to_dict(self) -> dict[str, Any]:
        provider_key = self.provider_key

        state = self.state

        state_version = self.state_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "provider_key": provider_key,
                "state": state,
                "state_version": state_version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        provider_key = d.pop("provider_key")

        state = d.pop("state")

        state_version = d.pop("state_version")

        environment_state = cls(
            provider_key=provider_key,
            state=state,
            state_version=state_version,
        )

        return environment_state
