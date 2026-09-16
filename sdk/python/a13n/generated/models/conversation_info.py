from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.conversation_info_audience import ConversationInfoAudience
from ..types import UNSET, Unset

T = TypeVar("T", bound="ConversationInfo")


@_attrs_define(repr=False)
class ConversationInfo:
    """
    Attributes:
        audience (ConversationInfoAudience):
        external (bool | None):
        id (str):
        is_active (bool | None):
        is_member (bool | None):
        name (str):
        organization_id (None | str | Unset):
    """

    audience: ConversationInfoAudience
    external: bool | None
    id: str
    is_active: bool | None
    is_member: bool | None
    name: str
    organization_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        audience = self.audience.value

        external: bool | None
        external = self.external

        id = self.id

        is_active: bool | None
        is_active = self.is_active

        is_member: bool | None
        is_member = self.is_member

        name = self.name

        organization_id: str | Unset | None
        if isinstance(self.organization_id, Unset):
            organization_id = UNSET
        else:
            organization_id = self.organization_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "audience": audience,
                "external": external,
                "id": id,
                "is_active": is_active,
                "is_member": is_member,
                "name": name,
            }
        )
        if organization_id is not UNSET:
            field_dict["organization_id"] = organization_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        audience = ConversationInfoAudience(d.pop("audience"))

        def _parse_external(data: object) -> bool | None:
            if data is None:
                return data
            return cast(bool | None, data)

        external = _parse_external(d.pop("external"))

        id = d.pop("id")

        def _parse_is_active(data: object) -> bool | None:
            if data is None:
                return data
            return cast(bool | None, data)

        is_active = _parse_is_active(d.pop("is_active"))

        def _parse_is_member(data: object) -> bool | None:
            if data is None:
                return data
            return cast(bool | None, data)

        is_member = _parse_is_member(d.pop("is_member"))

        name = d.pop("name")

        def _parse_organization_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        organization_id = _parse_organization_id(d.pop("organization_id", UNSET))

        conversation_info = cls(
            audience=audience,
            external=external,
            id=id,
            is_active=is_active,
            is_member=is_member,
            name=name,
            organization_id=organization_id,
        )

        return conversation_info
