from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="ReorderQueuedSubmissionsRequest")


@_attrs_define(repr=False)
class ReorderQueuedSubmissionsRequest:
    """
    Attributes:
        expected_queue_version (int):
        queued_submission_ids (list[str]):
    """

    expected_queue_version: int
    queued_submission_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        expected_queue_version = self.expected_queue_version

        queued_submission_ids = self.queued_submission_ids

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_queue_version": expected_queue_version,
                "queued_submission_ids": queued_submission_ids,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expected_queue_version = d.pop("expected_queue_version")

        queued_submission_ids = cast(list[str], d.pop("queued_submission_ids"))

        reorder_queued_submissions_request = cls(
            expected_queue_version=expected_queue_version,
            queued_submission_ids=queued_submission_ids,
        )

        return reorder_queued_submissions_request
