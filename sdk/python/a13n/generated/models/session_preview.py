from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.run_status import RunStatus

T = TypeVar("T", bound="SessionPreview")


@_attrs_define(repr=False)
class SessionPreview:
    """
    Attributes:
        agent_name (None | str):
        input_text (None | str):
        output_text (None | str):
        run_id (str):
        run_status (RunStatus):
        thread_id (str):
        trigger_type (str):
    """

    agent_name: str | None
    input_text: str | None
    output_text: str | None
    run_id: str
    run_status: RunStatus
    thread_id: str
    trigger_type: str

    def to_dict(self) -> dict[str, Any]:
        agent_name: str | None
        agent_name = self.agent_name

        input_text: str | None
        input_text = self.input_text

        output_text: str | None
        output_text = self.output_text

        run_id = self.run_id

        run_status = self.run_status.value

        thread_id = self.thread_id

        trigger_type = self.trigger_type

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "agent_name": agent_name,
                "input_text": input_text,
                "output_text": output_text,
                "run_id": run_id,
                "run_status": run_status,
                "thread_id": thread_id,
                "trigger_type": trigger_type,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_agent_name(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        agent_name = _parse_agent_name(d.pop("agent_name"))

        def _parse_input_text(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        input_text = _parse_input_text(d.pop("input_text"))

        def _parse_output_text(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        output_text = _parse_output_text(d.pop("output_text"))

        run_id = d.pop("run_id")

        run_status = RunStatus(d.pop("run_status"))

        thread_id = d.pop("thread_id")

        trigger_type = d.pop("trigger_type")

        session_preview = cls(
            agent_name=agent_name,
            input_text=input_text,
            output_text=output_text,
            run_id=run_id,
            run_status=run_status,
            thread_id=thread_id,
            trigger_type=trigger_type,
        )

        return session_preview
