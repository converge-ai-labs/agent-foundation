from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="ConfigurationSessionView")


@_attrs_define(repr=False)
class ConfigurationSessionView:
    """
    Attributes:
        created_at (datetime.datetime):
        id (str):
        organization_id (str):
        owner_user_id (str):
        root_thread_id (str):
        target_agent_id (None | str):
        updated_at (datetime.datetime):
        workspace_id (str):
    """

    created_at: datetime.datetime
    id: str
    organization_id: str
    owner_user_id: str
    root_thread_id: str
    target_agent_id: str | None
    updated_at: datetime.datetime
    workspace_id: str

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = self.id

        organization_id = self.organization_id

        owner_user_id = self.owner_user_id

        root_thread_id = self.root_thread_id

        target_agent_id: str | None
        target_agent_id = self.target_agent_id

        updated_at = self.updated_at.isoformat()

        workspace_id = self.workspace_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "organization_id": organization_id,
                "owner_user_id": owner_user_id,
                "root_thread_id": root_thread_id,
                "target_agent_id": target_agent_id,
                "updated_at": updated_at,
                "workspace_id": workspace_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        organization_id = d.pop("organization_id")

        owner_user_id = d.pop("owner_user_id")

        root_thread_id = d.pop("root_thread_id")

        def _parse_target_agent_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        target_agent_id = _parse_target_agent_id(d.pop("target_agent_id"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        workspace_id = d.pop("workspace_id")

        configuration_session_view = cls(
            created_at=created_at,
            id=id,
            organization_id=organization_id,
            owner_user_id=owner_user_id,
            root_thread_id=root_thread_id,
            target_agent_id=target_agent_id,
            updated_at=updated_at,
            workspace_id=workspace_id,
        )

        return configuration_session_view
