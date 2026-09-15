from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.sharing_policy_input_kinds_item import SharingPolicyInputKindsItem
from ..types import UNSET, Unset

T = TypeVar("T", bound="SharingPolicyInput")


@_attrs_define(repr=False)
class SharingPolicyInput:
    """
    Attributes:
        name (str):
        scope_ids (list[str]):
        enabled (bool | Unset):
        enroll_future_groups (bool | Unset):
        include_history (bool | Unset):
        kinds (list[SharingPolicyInputKindsItem] | Unset):
    """

    name: str
    scope_ids: list[str]
    enabled: bool | Unset = UNSET
    enroll_future_groups: bool | Unset = UNSET
    include_history: bool | Unset = UNSET
    kinds: list[SharingPolicyInputKindsItem] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        scope_ids = self.scope_ids

        enabled = self.enabled

        enroll_future_groups = self.enroll_future_groups

        include_history = self.include_history

        kinds: list[str] | Unset = UNSET
        if not isinstance(self.kinds, Unset):
            kinds = []
            for kinds_item_data in self.kinds:
                kinds_item = kinds_item_data.value
                kinds.append(kinds_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "name": name,
                "scope_ids": scope_ids,
            }
        )
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if enroll_future_groups is not UNSET:
            field_dict["enroll_future_groups"] = enroll_future_groups
        if include_history is not UNSET:
            field_dict["include_history"] = include_history
        if kinds is not UNSET:
            field_dict["kinds"] = kinds

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        scope_ids = cast(list[str], d.pop("scope_ids"))

        enabled = d.pop("enabled", UNSET)

        enroll_future_groups = d.pop("enroll_future_groups", UNSET)

        include_history = d.pop("include_history", UNSET)

        _kinds = d.pop("kinds", UNSET)
        kinds: list[SharingPolicyInputKindsItem] | Unset = UNSET
        if _kinds is not UNSET:
            kinds = []
            for kinds_item_data in _kinds:
                kinds_item = SharingPolicyInputKindsItem(kinds_item_data)

                kinds.append(kinds_item)

        sharing_policy_input = cls(
            name=name,
            scope_ids=scope_ids,
            enabled=enabled,
            enroll_future_groups=enroll_future_groups,
            include_history=include_history,
            kinds=kinds,
        )

        return sharing_policy_input
