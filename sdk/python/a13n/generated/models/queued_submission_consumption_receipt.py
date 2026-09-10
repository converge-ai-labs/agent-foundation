from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.queued_submission_consumption_receipt_outcome import QueuedSubmissionConsumptionReceiptOutcome
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.queued_submission import QueuedSubmission
    from ..models.run_acceptance_receipt import RunAcceptanceReceipt


T = TypeVar("T", bound="QueuedSubmissionConsumptionReceipt")


@_attrs_define(repr=False)
class QueuedSubmissionConsumptionReceipt:
    """
    Attributes:
        outcome (QueuedSubmissionConsumptionReceiptOutcome):
        queue_version (int):
        queued_submission (QueuedSubmission):
        run (None | RunAcceptanceReceipt | Unset):
    """

    outcome: QueuedSubmissionConsumptionReceiptOutcome
    queue_version: int
    queued_submission: QueuedSubmission
    run: RunAcceptanceReceipt | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.run_acceptance_receipt import RunAcceptanceReceipt

        outcome = self.outcome.value

        queue_version = self.queue_version

        queued_submission = self.queued_submission.to_dict()

        run: dict[str, Any] | Unset | None
        if isinstance(self.run, Unset):
            run = UNSET
        elif isinstance(self.run, RunAcceptanceReceipt):
            run = self.run.to_dict()
        else:
            run = self.run

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "outcome": outcome,
                "queue_version": queue_version,
                "queued_submission": queued_submission,
            }
        )
        if run is not UNSET:
            field_dict["run"] = run

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.queued_submission import QueuedSubmission
        from ..models.run_acceptance_receipt import RunAcceptanceReceipt

        d = dict(src_dict)
        outcome = QueuedSubmissionConsumptionReceiptOutcome(d.pop("outcome"))

        queue_version = d.pop("queue_version")

        queued_submission = QueuedSubmission.from_dict(d.pop("queued_submission"))

        def _parse_run(data: object) -> RunAcceptanceReceipt | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                run_type_0 = RunAcceptanceReceipt.from_dict(data)

                return run_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(RunAcceptanceReceipt | Unset | None, data)

        run = _parse_run(d.pop("run", UNSET))

        queued_submission_consumption_receipt = cls(
            outcome=outcome,
            queue_version=queue_version,
            queued_submission=queued_submission,
            run=run,
        )

        return queued_submission_consumption_receipt
