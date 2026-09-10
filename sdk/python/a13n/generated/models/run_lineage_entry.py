from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.run_lineage_kind import RunLineageKind
from ..models.run_status import RunStatus

T = TypeVar("T", bound="RunLineageEntry")


@_attrs_define(repr=False)
class RunLineageEntry:
    """
    Attributes:
        created_at (datetime.datetime):
        depth_from_head (int):
        lineage_kind (RunLineageKind):
        parent_run_id (None | str):
        run_id (str):
        session_id (str):
        status (RunStatus):
        thread_id (str):
    """

    created_at: datetime.datetime
    depth_from_head: int
    lineage_kind: RunLineageKind
    parent_run_id: str | None
    run_id: str
    session_id: str
    status: RunStatus
    thread_id: str

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        depth_from_head = self.depth_from_head

        lineage_kind = self.lineage_kind.value

        parent_run_id: str | None
        parent_run_id = self.parent_run_id

        run_id = self.run_id

        session_id = self.session_id

        status = self.status.value

        thread_id = self.thread_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "created_at": created_at,
                "depth_from_head": depth_from_head,
                "lineage_kind": lineage_kind,
                "parent_run_id": parent_run_id,
                "run_id": run_id,
                "session_id": session_id,
                "status": status,
                "thread_id": thread_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        depth_from_head = d.pop("depth_from_head")

        lineage_kind = RunLineageKind(d.pop("lineage_kind"))

        def _parse_parent_run_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        parent_run_id = _parse_parent_run_id(d.pop("parent_run_id"))

        run_id = d.pop("run_id")

        session_id = d.pop("session_id")

        status = RunStatus(d.pop("status"))

        thread_id = d.pop("thread_id")

        run_lineage_entry = cls(
            created_at=created_at,
            depth_from_head=depth_from_head,
            lineage_kind=lineage_kind,
            parent_run_id=parent_run_id,
            run_id=run_id,
            session_id=session_id,
            status=status,
            thread_id=thread_id,
        )

        return run_lineage_entry
