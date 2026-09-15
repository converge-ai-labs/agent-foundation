from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="MemoryProviderReference")


@_attrs_define(repr=False)
class MemoryProviderReference:
    """
    Attributes:
        agent_id (str):
        agent_revision_id (str):
        is_current (bool):
        version (int):
    """

    agent_id: str
    agent_revision_id: str
    is_current: bool
    version: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent_id = self.agent_id

        agent_revision_id = self.agent_revision_id

        is_current = self.is_current

        version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent_id": agent_id,
                "agent_revision_id": agent_revision_id,
                "is_current": is_current,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agent_id = d.pop("agent_id")

        agent_revision_id = d.pop("agent_revision_id")

        is_current = d.pop("is_current")

        version = d.pop("version")

        memory_provider_reference = cls(
            agent_id=agent_id,
            agent_revision_id=agent_revision_id,
            is_current=is_current,
            version=version,
        )

        memory_provider_reference.additional_properties = d
        return memory_provider_reference

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
