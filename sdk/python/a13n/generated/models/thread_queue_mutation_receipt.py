from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="ThreadQueueMutationReceipt")


@_attrs_define(repr=False)
class ThreadQueueMutationReceipt:
    """
    Attributes:
        queue_version (int):
        thread_id (str):
    """

    queue_version: int
    thread_id: str

    def to_dict(self) -> dict[str, Any]:
        queue_version = self.queue_version

        thread_id = self.thread_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "queue_version": queue_version,
                "thread_id": thread_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        queue_version = d.pop("queue_version")

        thread_id = d.pop("thread_id")

        thread_queue_mutation_receipt = cls(
            queue_version=queue_version,
            thread_id=thread_id,
        )

        return thread_queue_mutation_receipt
