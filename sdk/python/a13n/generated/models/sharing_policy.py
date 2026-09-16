from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.sharing_policy_kinds_item import SharingPolicyKindsItem
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.sharing_participant import SharingParticipant


T = TypeVar("T", bound="SharingPolicy")


@_attrs_define(repr=False)
class SharingPolicy:
    """
    Attributes:
        created_at (datetime.datetime):
        future_since (datetime.datetime):
        id (str):
        name (str):
        participants (list[SharingParticipant]):
        scope_ids (list[str]):
        version (int):
        enabled (bool | Unset):
        enroll_future_groups (bool | Unset):
        include_history (bool | Unset):
        kinds (list[SharingPolicyKindsItem] | Unset):
    """

    created_at: datetime.datetime
    future_since: datetime.datetime
    id: str
    name: str
    participants: list[SharingParticipant]
    scope_ids: list[str]
    version: int
    enabled: bool | Unset = UNSET
    enroll_future_groups: bool | Unset = UNSET
    include_history: bool | Unset = UNSET
    kinds: list[SharingPolicyKindsItem] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        future_since = self.future_since.isoformat()

        id = self.id

        name = self.name

        participants = []
        for participants_item_data in self.participants:
            participants_item = participants_item_data.to_dict()
            participants.append(participants_item)

        scope_ids = self.scope_ids

        version = self.version

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
                "created_at": created_at,
                "future_since": future_since,
                "id": id,
                "name": name,
                "participants": participants,
                "scope_ids": scope_ids,
                "version": version,
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
        from ..models.sharing_participant import SharingParticipant

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        future_since = datetime.datetime.fromisoformat(d.pop("future_since"))

        id = d.pop("id")

        name = d.pop("name")

        participants = []
        _participants = d.pop("participants")
        for participants_item_data in _participants:
            participants_item = SharingParticipant.from_dict(participants_item_data)

            participants.append(participants_item)

        scope_ids = cast(list[str], d.pop("scope_ids"))

        version = d.pop("version")

        enabled = d.pop("enabled", UNSET)

        enroll_future_groups = d.pop("enroll_future_groups", UNSET)

        include_history = d.pop("include_history", UNSET)

        _kinds = d.pop("kinds", UNSET)
        kinds: list[SharingPolicyKindsItem] | Unset = UNSET
        if _kinds is not UNSET:
            kinds = []
            for kinds_item_data in _kinds:
                kinds_item = SharingPolicyKindsItem(kinds_item_data)

                kinds.append(kinds_item)

        sharing_policy = cls(
            created_at=created_at,
            future_since=future_since,
            id=id,
            name=name,
            participants=participants,
            scope_ids=scope_ids,
            version=version,
            enabled=enabled,
            enroll_future_groups=enroll_future_groups,
            include_history=include_history,
            kinds=kinds,
        )

        return sharing_policy
