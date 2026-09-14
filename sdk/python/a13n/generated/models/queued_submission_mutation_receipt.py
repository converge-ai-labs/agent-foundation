from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.queued_submission import QueuedSubmission


T = TypeVar("T", bound="QueuedSubmissionMutationReceipt")


@_attrs_define(repr=False)
class QueuedSubmissionMutationReceipt:
    """
    Attributes:
        queue_version (int):
        queued_submission (QueuedSubmission):
    """

    queue_version: int
    queued_submission: QueuedSubmission

    def to_dict(self) -> dict[str, Any]:
        queue_version = self.queue_version

        queued_submission = self.queued_submission.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "queue_version": queue_version,
                "queued_submission": queued_submission,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.queued_submission import QueuedSubmission

        d = dict(src_dict)
        queue_version = d.pop("queue_version")

        queued_submission = QueuedSubmission.from_dict(d.pop("queued_submission"))

        queued_submission_mutation_receipt = cls(
            queue_version=queue_version,
            queued_submission=queued_submission,
        )

        return queued_submission_mutation_receipt
