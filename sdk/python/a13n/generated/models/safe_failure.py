from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..models.safe_failure_retry_hint import SafeFailureRetryHint
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.safe_failure_details import SafeFailureDetails


T = TypeVar("T", bound="SafeFailure")


@_attrs_define(repr=False)
class SafeFailure:
    """Bounded serializable failure safe to expose outside the process.

    Attributes:
        code (str):
        details (SafeFailureDetails): Return detached safe failure details.
        message (str):
        retry_hint (SafeFailureRetryHint | Unset):
    """

    code: str
    details: SafeFailureDetails
    message: str
    retry_hint: SafeFailureRetryHint | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        code = self.code

        details = self.details.to_dict()

        message = self.message

        retry_hint: str | Unset = UNSET
        if not isinstance(self.retry_hint, Unset):
            retry_hint = self.retry_hint.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "code": code,
                "details": details,
                "message": message,
            }
        )
        if retry_hint is not UNSET:
            field_dict["retry_hint"] = retry_hint

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.safe_failure_details import SafeFailureDetails

        d = dict(src_dict)
        code = d.pop("code")

        details = SafeFailureDetails.from_dict(d.pop("details"))

        message = d.pop("message")

        _retry_hint = d.pop("retry_hint", UNSET)
        retry_hint: SafeFailureRetryHint | Unset
        if isinstance(_retry_hint, Unset):
            retry_hint = UNSET
        else:
            retry_hint = SafeFailureRetryHint(_retry_hint)

        safe_failure = cls(
            code=code,
            details=details,
            message=message,
            retry_hint=retry_hint,
        )

        return safe_failure
