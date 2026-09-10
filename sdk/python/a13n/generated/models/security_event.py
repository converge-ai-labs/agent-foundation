from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="SecurityEvent")


@_attrs_define(repr=False)
class SecurityEvent:
    """
    Attributes:
        action (str):
        actor_id (None | str):
        actor_type (str):
        id (str):
        occurred_at (datetime.datetime):
        organization_id (None | str):
        outcome (str):
        request_id (None | str):
        resource_id (None | str):
        resource_type (None | str):
        workspace_id (None | str):
    """

    action: str
    actor_id: str | None
    actor_type: str
    id: str
    occurred_at: datetime.datetime
    organization_id: str | None
    outcome: str
    request_id: str | None
    resource_id: str | None
    resource_type: str | None
    workspace_id: str | None
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        action = self.action

        actor_id: str | None
        actor_id = self.actor_id

        actor_type = self.actor_type

        id = self.id

        occurred_at = self.occurred_at.isoformat()

        organization_id: str | None
        organization_id = self.organization_id

        outcome = self.outcome

        request_id: str | None
        request_id = self.request_id

        resource_id: str | None
        resource_id = self.resource_id

        resource_type: str | None
        resource_type = self.resource_type

        workspace_id: str | None
        workspace_id = self.workspace_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "action": action,
                "actor_id": actor_id,
                "actor_type": actor_type,
                "id": id,
                "occurred_at": occurred_at,
                "organization_id": organization_id,
                "outcome": outcome,
                "request_id": request_id,
                "resource_id": resource_id,
                "resource_type": resource_type,
                "workspace_id": workspace_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        action = d.pop("action")

        def _parse_actor_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        actor_id = _parse_actor_id(d.pop("actor_id"))

        actor_type = d.pop("actor_type")

        id = d.pop("id")

        occurred_at = datetime.datetime.fromisoformat(d.pop("occurred_at"))

        def _parse_organization_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        organization_id = _parse_organization_id(d.pop("organization_id"))

        outcome = d.pop("outcome")

        def _parse_request_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        request_id = _parse_request_id(d.pop("request_id"))

        def _parse_resource_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        resource_id = _parse_resource_id(d.pop("resource_id"))

        def _parse_resource_type(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        resource_type = _parse_resource_type(d.pop("resource_type"))

        def _parse_workspace_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        workspace_id = _parse_workspace_id(d.pop("workspace_id"))

        security_event = cls(
            action=action,
            actor_id=actor_id,
            actor_type=actor_type,
            id=id,
            occurred_at=occurred_at,
            organization_id=organization_id,
            outcome=outcome,
            request_id=request_id,
            resource_id=resource_id,
            resource_type=resource_type,
            workspace_id=workspace_id,
        )

        security_event.additional_properties = d
        return security_event

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
