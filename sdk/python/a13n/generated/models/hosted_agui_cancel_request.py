from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="HostedAguiCancelRequest")


@_attrs_define(repr=False)
class HostedAguiCancelRequest:
    """
    Attributes:
        run_id (str):
        thread_id (str):
    """

    run_id: str
    thread_id: str

    def to_dict(self) -> dict[str, Any]:
        run_id = self.run_id

        thread_id = self.thread_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "runId": run_id,
                "threadId": thread_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        run_id = d.pop("runId")

        thread_id = d.pop("threadId")

        hosted_agui_cancel_request = cls(
            run_id=run_id,
            thread_id=thread_id,
        )

        return hosted_agui_cancel_request
