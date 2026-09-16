from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.thread_resource_labels import ThreadResourceLabels


T = TypeVar("T", bound="ThreadResource")


@_attrs_define(repr=False)
class ThreadResource:
    """
    Attributes:
        created_at (datetime.datetime):
        current_run_id (None | str):
        default_environment_id (None | str):
        head_run_id (None | str):
        id (str):
        labels (ThreadResourceLabels):
        origin_kind (str):
        origin_run_id (None | str):
        origin_thread_id (None | str):
        queue_version (int):
        role (str):
        session_id (str):
        updated_at (datetime.datetime):
        version (int):
        configuration_draft_id (None | str | Unset):
    """

    created_at: datetime.datetime
    current_run_id: str | None
    default_environment_id: str | None
    head_run_id: str | None
    id: str
    labels: ThreadResourceLabels
    origin_kind: str
    origin_run_id: str | None
    origin_thread_id: str | None
    queue_version: int
    role: str
    session_id: str
    updated_at: datetime.datetime
    version: int
    configuration_draft_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        current_run_id: str | None
        current_run_id = self.current_run_id

        default_environment_id: str | None
        default_environment_id = self.default_environment_id

        head_run_id: str | None
        head_run_id = self.head_run_id

        id = self.id

        labels = self.labels.to_dict()

        origin_kind = self.origin_kind

        origin_run_id: str | None
        origin_run_id = self.origin_run_id

        origin_thread_id: str | None
        origin_thread_id = self.origin_thread_id

        queue_version = self.queue_version

        role = self.role

        session_id = self.session_id

        updated_at = self.updated_at.isoformat()

        version = self.version

        configuration_draft_id: str | Unset | None
        if isinstance(self.configuration_draft_id, Unset):
            configuration_draft_id = UNSET
        else:
            configuration_draft_id = self.configuration_draft_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "created_at": created_at,
                "current_run_id": current_run_id,
                "default_environment_id": default_environment_id,
                "head_run_id": head_run_id,
                "id": id,
                "labels": labels,
                "origin_kind": origin_kind,
                "origin_run_id": origin_run_id,
                "origin_thread_id": origin_thread_id,
                "queue_version": queue_version,
                "role": role,
                "session_id": session_id,
                "updated_at": updated_at,
                "version": version,
            }
        )
        if configuration_draft_id is not UNSET:
            field_dict["configuration_draft_id"] = configuration_draft_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.thread_resource_labels import ThreadResourceLabels

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        def _parse_current_run_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        current_run_id = _parse_current_run_id(d.pop("current_run_id"))

        def _parse_default_environment_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        default_environment_id = _parse_default_environment_id(d.pop("default_environment_id"))

        def _parse_head_run_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        head_run_id = _parse_head_run_id(d.pop("head_run_id"))

        id = d.pop("id")

        labels = ThreadResourceLabels.from_dict(d.pop("labels"))

        origin_kind = d.pop("origin_kind")

        def _parse_origin_run_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        origin_run_id = _parse_origin_run_id(d.pop("origin_run_id"))

        def _parse_origin_thread_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        origin_thread_id = _parse_origin_thread_id(d.pop("origin_thread_id"))

        queue_version = d.pop("queue_version")

        role = d.pop("role")

        session_id = d.pop("session_id")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        version = d.pop("version")

        def _parse_configuration_draft_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        configuration_draft_id = _parse_configuration_draft_id(d.pop("configuration_draft_id", UNSET))

        thread_resource = cls(
            created_at=created_at,
            current_run_id=current_run_id,
            default_environment_id=default_environment_id,
            head_run_id=head_run_id,
            id=id,
            labels=labels,
            origin_kind=origin_kind,
            origin_run_id=origin_run_id,
            origin_thread_id=origin_thread_id,
            queue_version=queue_version,
            role=role,
            session_id=session_id,
            updated_at=updated_at,
            version=version,
            configuration_draft_id=configuration_draft_id,
        )

        return thread_resource
