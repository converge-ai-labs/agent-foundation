from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.thread_origin_kind import ThreadOriginKind
from ..models.thread_role import ThreadRole
from ..types import UNSET, Unset

T = TypeVar("T", bound="Thread")


@_attrs_define(repr=False)
class Thread:
    """
    Attributes:
        created_at (datetime.datetime):
        id (str):
        organization_id (str):
        origin_kind (ThreadOriginKind):
        queue_version (int):
        role (ThreadRole):
        session_id (str):
        updated_at (datetime.datetime):
        version (int):
        current_run_id (None | str | Unset):
        default_environment_id (None | str | Unset):
        head_run_id (None | str | Unset):
        origin_run_id (None | str | Unset):
        origin_thread_id (None | str | Unset):
    """

    created_at: datetime.datetime
    id: str
    organization_id: str
    origin_kind: ThreadOriginKind
    queue_version: int
    role: ThreadRole
    session_id: str
    updated_at: datetime.datetime
    version: int
    current_run_id: str | Unset | None = UNSET
    default_environment_id: str | Unset | None = UNSET
    head_run_id: str | Unset | None = UNSET
    origin_run_id: str | Unset | None = UNSET
    origin_thread_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = self.id

        organization_id = self.organization_id

        origin_kind = self.origin_kind.value

        queue_version = self.queue_version

        role = self.role.value

        session_id = self.session_id

        updated_at = self.updated_at.isoformat()

        version = self.version

        current_run_id: str | Unset | None
        if isinstance(self.current_run_id, Unset):
            current_run_id = UNSET
        else:
            current_run_id = self.current_run_id

        default_environment_id: str | Unset | None
        if isinstance(self.default_environment_id, Unset):
            default_environment_id = UNSET
        else:
            default_environment_id = self.default_environment_id

        head_run_id: str | Unset | None
        if isinstance(self.head_run_id, Unset):
            head_run_id = UNSET
        else:
            head_run_id = self.head_run_id

        origin_run_id: str | Unset | None
        if isinstance(self.origin_run_id, Unset):
            origin_run_id = UNSET
        else:
            origin_run_id = self.origin_run_id

        origin_thread_id: str | Unset | None
        if isinstance(self.origin_thread_id, Unset):
            origin_thread_id = UNSET
        else:
            origin_thread_id = self.origin_thread_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "organization_id": organization_id,
                "origin_kind": origin_kind,
                "queue_version": queue_version,
                "role": role,
                "session_id": session_id,
                "updated_at": updated_at,
                "version": version,
            }
        )
        if current_run_id is not UNSET:
            field_dict["current_run_id"] = current_run_id
        if default_environment_id is not UNSET:
            field_dict["default_environment_id"] = default_environment_id
        if head_run_id is not UNSET:
            field_dict["head_run_id"] = head_run_id
        if origin_run_id is not UNSET:
            field_dict["origin_run_id"] = origin_run_id
        if origin_thread_id is not UNSET:
            field_dict["origin_thread_id"] = origin_thread_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        organization_id = d.pop("organization_id")

        origin_kind = ThreadOriginKind(d.pop("origin_kind"))

        queue_version = d.pop("queue_version")

        role = ThreadRole(d.pop("role"))

        session_id = d.pop("session_id")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        version = d.pop("version")

        def _parse_current_run_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        current_run_id = _parse_current_run_id(d.pop("current_run_id", UNSET))

        def _parse_default_environment_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        default_environment_id = _parse_default_environment_id(d.pop("default_environment_id", UNSET))

        def _parse_head_run_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        head_run_id = _parse_head_run_id(d.pop("head_run_id", UNSET))

        def _parse_origin_run_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        origin_run_id = _parse_origin_run_id(d.pop("origin_run_id", UNSET))

        def _parse_origin_thread_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        origin_thread_id = _parse_origin_thread_id(d.pop("origin_thread_id", UNSET))

        thread = cls(
            created_at=created_at,
            id=id,
            organization_id=organization_id,
            origin_kind=origin_kind,
            queue_version=queue_version,
            role=role,
            session_id=session_id,
            updated_at=updated_at,
            version=version,
            current_run_id=current_run_id,
            default_environment_id=default_environment_id,
            head_run_id=head_run_id,
            origin_run_id=origin_run_id,
            origin_thread_id=origin_thread_id,
        )

        return thread
