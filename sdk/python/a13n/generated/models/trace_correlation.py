from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="TraceCorrelation")


@_attrs_define(repr=False)
class TraceCorrelation:
    """
    Attributes:
        agent_id (str):
        organization_id (str):
        run_attempt_id (str):
        run_id (str):
        session_id (str):
        thread_id (str):
        workspace_id (str):
    """

    agent_id: str
    organization_id: str
    run_attempt_id: str
    run_id: str
    session_id: str
    thread_id: str
    workspace_id: str

    def to_dict(self) -> dict[str, Any]:
        agent_id = self.agent_id

        organization_id = self.organization_id

        run_attempt_id = self.run_attempt_id

        run_id = self.run_id

        session_id = self.session_id

        thread_id = self.thread_id

        workspace_id = self.workspace_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "agent_id": agent_id,
                "organization_id": organization_id,
                "run_attempt_id": run_attempt_id,
                "run_id": run_id,
                "session_id": session_id,
                "thread_id": thread_id,
                "workspace_id": workspace_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agent_id = d.pop("agent_id")

        organization_id = d.pop("organization_id")

        run_attempt_id = d.pop("run_attempt_id")

        run_id = d.pop("run_id")

        session_id = d.pop("session_id")

        thread_id = d.pop("thread_id")

        workspace_id = d.pop("workspace_id")

        trace_correlation = cls(
            agent_id=agent_id,
            organization_id=organization_id,
            run_attempt_id=run_attempt_id,
            run_id=run_id,
            session_id=session_id,
            thread_id=thread_id,
            workspace_id=workspace_id,
        )

        return trace_correlation
