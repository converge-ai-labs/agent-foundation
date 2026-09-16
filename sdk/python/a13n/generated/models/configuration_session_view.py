from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ConfigurationSessionView")


@_attrs_define(repr=False)
class ConfigurationSessionView:
    """
    Attributes:
        configuration_draft_id (str):
        created_at (datetime.datetime):
        id (str):
        organization_id (str):
        owner_user_id (str):
        root_thread_id (str):
        updated_at (datetime.datetime):
        workspace_id (str):
        has_runs (bool | Unset):
        title (None | str | Unset):
    """

    configuration_draft_id: str
    created_at: datetime.datetime
    id: str
    organization_id: str
    owner_user_id: str
    root_thread_id: str
    updated_at: datetime.datetime
    workspace_id: str
    has_runs: bool | Unset = UNSET
    title: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        configuration_draft_id = self.configuration_draft_id

        created_at = self.created_at.isoformat()

        id = self.id

        organization_id = self.organization_id

        owner_user_id = self.owner_user_id

        root_thread_id = self.root_thread_id

        updated_at = self.updated_at.isoformat()

        workspace_id = self.workspace_id

        has_runs = self.has_runs

        title: str | Unset | None
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "configuration_draft_id": configuration_draft_id,
                "created_at": created_at,
                "id": id,
                "organization_id": organization_id,
                "owner_user_id": owner_user_id,
                "root_thread_id": root_thread_id,
                "updated_at": updated_at,
                "workspace_id": workspace_id,
            }
        )
        if has_runs is not UNSET:
            field_dict["has_runs"] = has_runs
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        configuration_draft_id = d.pop("configuration_draft_id")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        organization_id = d.pop("organization_id")

        owner_user_id = d.pop("owner_user_id")

        root_thread_id = d.pop("root_thread_id")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        workspace_id = d.pop("workspace_id")

        has_runs = d.pop("has_runs", UNSET)

        def _parse_title(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        title = _parse_title(d.pop("title", UNSET))

        configuration_session_view = cls(
            configuration_draft_id=configuration_draft_id,
            created_at=created_at,
            id=id,
            organization_id=organization_id,
            owner_user_id=owner_user_id,
            root_thread_id=root_thread_id,
            updated_at=updated_at,
            workspace_id=workspace_id,
            has_runs=has_runs,
            title=title,
        )

        return configuration_session_view
