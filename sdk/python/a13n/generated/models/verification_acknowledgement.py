from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.verification_acknowledgement_outcome import VerificationAcknowledgementOutcome

T = TypeVar("T", bound="VerificationAcknowledgement")


@_attrs_define(repr=False)
class VerificationAcknowledgement:
    """
    Attributes:
        outcome (VerificationAcknowledgementOutcome):
        reason (str):
    """

    outcome: VerificationAcknowledgementOutcome
    reason: str

    def to_dict(self) -> dict[str, Any]:
        outcome = self.outcome.value

        reason = self.reason

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "outcome": outcome,
                "reason": reason,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        outcome = VerificationAcknowledgementOutcome(d.pop("outcome"))

        reason = d.pop("reason")

        verification_acknowledgement = cls(
            outcome=outcome,
            reason=reason,
        )

        return verification_acknowledgement
