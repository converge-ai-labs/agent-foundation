from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.queued_submission_state import QueuedSubmissionState
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.principal_ref import PrincipalRef
    from ..models.queued_submission_failure import QueuedSubmissionFailure
    from ..models.thread_run_submission_intent_output import ThreadRunSubmissionIntentOutput


T = TypeVar("T", bound="QueuedSubmission")


@_attrs_define(repr=False)
class QueuedSubmission:
    """
    Attributes:
        authority_principal (PrincipalRef):
        created_at (datetime.datetime):
        queued_submission_id (str):
        state (QueuedSubmissionState):
        submission (ThreadRunSubmissionIntentOutput):
        submission_digest_sha256 (str):
        thread_id (str):
        updated_at (datetime.datetime):
        version (int):
        consumed_at (datetime.datetime | None | Unset):
        consumed_run_id (None | str | Unset):
        failed_at (datetime.datetime | None | Unset):
        failure (None | QueuedSubmissionFailure | Unset):
        position (int | None | Unset):
    """

    authority_principal: PrincipalRef
    created_at: datetime.datetime
    queued_submission_id: str
    state: QueuedSubmissionState
    submission: ThreadRunSubmissionIntentOutput
    submission_digest_sha256: str
    thread_id: str
    updated_at: datetime.datetime
    version: int
    consumed_at: datetime.datetime | Unset | None = UNSET
    consumed_run_id: str | Unset | None = UNSET
    failed_at: datetime.datetime | Unset | None = UNSET
    failure: QueuedSubmissionFailure | Unset | None = UNSET
    position: int | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.queued_submission_failure import QueuedSubmissionFailure

        authority_principal = self.authority_principal.to_dict()

        created_at = self.created_at.isoformat()

        queued_submission_id = self.queued_submission_id

        state = self.state.value

        submission = self.submission.to_dict()

        submission_digest_sha256 = self.submission_digest_sha256

        thread_id = self.thread_id

        updated_at = self.updated_at.isoformat()

        version = self.version

        consumed_at: str | Unset | None
        if isinstance(self.consumed_at, Unset):
            consumed_at = UNSET
        elif isinstance(self.consumed_at, datetime.datetime):
            consumed_at = self.consumed_at.isoformat()
        else:
            consumed_at = self.consumed_at

        consumed_run_id: str | Unset | None
        if isinstance(self.consumed_run_id, Unset):
            consumed_run_id = UNSET
        else:
            consumed_run_id = self.consumed_run_id

        failed_at: str | Unset | None
        if isinstance(self.failed_at, Unset):
            failed_at = UNSET
        elif isinstance(self.failed_at, datetime.datetime):
            failed_at = self.failed_at.isoformat()
        else:
            failed_at = self.failed_at

        failure: dict[str, Any] | Unset | None
        if isinstance(self.failure, Unset):
            failure = UNSET
        elif isinstance(self.failure, QueuedSubmissionFailure):
            failure = self.failure.to_dict()
        else:
            failure = self.failure

        position: int | Unset | None
        if isinstance(self.position, Unset):
            position = UNSET
        else:
            position = self.position

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "authority_principal": authority_principal,
                "created_at": created_at,
                "queued_submission_id": queued_submission_id,
                "state": state,
                "submission": submission,
                "submission_digest_sha256": submission_digest_sha256,
                "thread_id": thread_id,
                "updated_at": updated_at,
                "version": version,
            }
        )
        if consumed_at is not UNSET:
            field_dict["consumed_at"] = consumed_at
        if consumed_run_id is not UNSET:
            field_dict["consumed_run_id"] = consumed_run_id
        if failed_at is not UNSET:
            field_dict["failed_at"] = failed_at
        if failure is not UNSET:
            field_dict["failure"] = failure
        if position is not UNSET:
            field_dict["position"] = position

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.principal_ref import PrincipalRef
        from ..models.queued_submission_failure import QueuedSubmissionFailure
        from ..models.thread_run_submission_intent_output import ThreadRunSubmissionIntentOutput

        d = dict(src_dict)
        authority_principal = PrincipalRef.from_dict(d.pop("authority_principal"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        queued_submission_id = d.pop("queued_submission_id")

        state = QueuedSubmissionState(d.pop("state"))

        submission = ThreadRunSubmissionIntentOutput.from_dict(d.pop("submission"))

        submission_digest_sha256 = d.pop("submission_digest_sha256")

        thread_id = d.pop("thread_id")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        version = d.pop("version")

        def _parse_consumed_at(data: object) -> datetime.datetime | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                consumed_at_type_0 = datetime.datetime.fromisoformat(data)

                return consumed_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | Unset | None, data)

        consumed_at = _parse_consumed_at(d.pop("consumed_at", UNSET))

        def _parse_consumed_run_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        consumed_run_id = _parse_consumed_run_id(d.pop("consumed_run_id", UNSET))

        def _parse_failed_at(data: object) -> datetime.datetime | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                failed_at_type_0 = datetime.datetime.fromisoformat(data)

                return failed_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | Unset | None, data)

        failed_at = _parse_failed_at(d.pop("failed_at", UNSET))

        def _parse_failure(data: object) -> QueuedSubmissionFailure | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                failure_type_0 = QueuedSubmissionFailure.from_dict(data)

                return failure_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(QueuedSubmissionFailure | Unset | None, data)

        failure = _parse_failure(d.pop("failure", UNSET))

        def _parse_position(data: object) -> int | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | Unset | None, data)

        position = _parse_position(d.pop("position", UNSET))

        queued_submission = cls(
            authority_principal=authority_principal,
            created_at=created_at,
            queued_submission_id=queued_submission_id,
            state=state,
            submission=submission,
            submission_digest_sha256=submission_digest_sha256,
            thread_id=thread_id,
            updated_at=updated_at,
            version=version,
            consumed_at=consumed_at,
            consumed_run_id=consumed_run_id,
            failed_at=failed_at,
            failure=failure,
            position=position,
        )

        return queued_submission
