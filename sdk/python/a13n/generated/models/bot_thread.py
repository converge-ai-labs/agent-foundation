from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.run_status import RunStatus

T = TypeVar("T", bound="BotThread")


@_attrs_define(repr=False)
class BotThread:
    """
    Attributes:
        agent_id (str):
        binding_id (str):
        run_id (str):
        run_status (RunStatus):
        session_id (str):
        thread_id (str):
        updated_at (datetime.datetime):
    """

    agent_id: str
    binding_id: str
    run_id: str
    run_status: RunStatus
    session_id: str
    thread_id: str
    updated_at: datetime.datetime

    def to_dict(self) -> dict[str, Any]:
        agent_id = self.agent_id

        binding_id = self.binding_id

        run_id = self.run_id

        run_status = self.run_status.value

        session_id = self.session_id

        thread_id = self.thread_id

        updated_at = self.updated_at.isoformat()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "agent_id": agent_id,
                "binding_id": binding_id,
                "run_id": run_id,
                "run_status": run_status,
                "session_id": session_id,
                "thread_id": thread_id,
                "updated_at": updated_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agent_id = d.pop("agent_id")

        binding_id = d.pop("binding_id")

        run_id = d.pop("run_id")

        run_status = RunStatus(d.pop("run_status"))

        session_id = d.pop("session_id")

        thread_id = d.pop("thread_id")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        bot_thread = cls(
            agent_id=agent_id,
            binding_id=binding_id,
            run_id=run_id,
            run_status=run_status,
            session_id=session_id,
            thread_id=thread_id,
            updated_at=updated_at,
        )

        return bot_thread
