from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ModelConnectionTestResult")


@_attrs_define(repr=False)
class ModelConnectionTestResult:
    """
    Attributes:
        code (str):
        elapsed_ms (int):
        message (str):
        success (bool):
        may_consume_quota_or_incur_cost (bool | Unset):
    """

    code: str
    elapsed_ms: int
    message: str
    success: bool
    may_consume_quota_or_incur_cost: bool | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        code = self.code

        elapsed_ms = self.elapsed_ms

        message = self.message

        success = self.success

        may_consume_quota_or_incur_cost = self.may_consume_quota_or_incur_cost

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "code": code,
                "elapsed_ms": elapsed_ms,
                "message": message,
                "success": success,
            }
        )
        if may_consume_quota_or_incur_cost is not UNSET:
            field_dict["may_consume_quota_or_incur_cost"] = may_consume_quota_or_incur_cost

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        code = d.pop("code")

        elapsed_ms = d.pop("elapsed_ms")

        message = d.pop("message")

        success = d.pop("success")

        may_consume_quota_or_incur_cost = d.pop("may_consume_quota_or_incur_cost", UNSET)

        model_connection_test_result = cls(
            code=code,
            elapsed_ms=elapsed_ms,
            message=message,
            success=success,
            may_consume_quota_or_incur_cost=may_consume_quota_or_incur_cost,
        )

        return model_connection_test_result
