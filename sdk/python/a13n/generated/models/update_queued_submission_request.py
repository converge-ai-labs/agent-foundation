from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.thread_run_submission_intent_input import ThreadRunSubmissionIntentInput


T = TypeVar("T", bound="UpdateQueuedSubmissionRequest")


@_attrs_define(repr=False)
class UpdateQueuedSubmissionRequest:
    """
    Attributes:
        expected_version (int):
        submission (ThreadRunSubmissionIntentInput):
    """

    expected_version: int
    submission: ThreadRunSubmissionIntentInput

    def to_dict(self) -> dict[str, Any]:
        expected_version = self.expected_version

        submission = self.submission.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
                "submission": submission,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.thread_run_submission_intent_input import ThreadRunSubmissionIntentInput

        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        submission = ThreadRunSubmissionIntentInput.from_dict(d.pop("submission"))

        update_queued_submission_request = cls(
            expected_version=expected_version,
            submission=submission,
        )

        return update_queued_submission_request
