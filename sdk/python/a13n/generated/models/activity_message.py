from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.activity_message_content import ActivityMessageContent


T = TypeVar("T", bound="ActivityMessage")


@_attrs_define(repr=False)
class ActivityMessage:
    """An activity progress message emitted between chat messages.

    Attributes:
        activity_type (str):
        content (ActivityMessageContent):
        id (str):
        role (Literal['activity'] | Unset):
    """

    activity_type: str
    content: ActivityMessageContent
    id: str
    role: Literal["activity"] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        activity_type = self.activity_type

        content = self.content.to_dict()

        id = self.id

        role = self.role

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "activityType": activity_type,
                "content": content,
                "id": id,
            }
        )
        if role is not UNSET:
            field_dict["role"] = role

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.activity_message_content import ActivityMessageContent

        d = dict(src_dict)
        activity_type = d.pop("activityType")

        content = ActivityMessageContent.from_dict(d.pop("content"))

        id = d.pop("id")

        role = cast(Literal["activity"] | Unset, d.pop("role", UNSET))
        if role != "activity" and not isinstance(role, Unset):
            raise ValueError(f"role must match const 'activity', got '{role}'")

        activity_message = cls(
            activity_type=activity_type,
            content=content,
            id=id,
            role=role,
        )

        activity_message.additional_properties = d
        return activity_message

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
