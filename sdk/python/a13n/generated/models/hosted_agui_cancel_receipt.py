from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="HostedAguiCancelReceipt")


@_attrs_define(repr=False)
class HostedAguiCancelReceipt:
    """
    Attributes:
        run_id (str):
        thread_id (str):
        schema_version (Literal['1'] | Unset):
        status (Literal['cancelled'] | Unset):
    """

    run_id: str
    thread_id: str
    schema_version: Literal["1"] | Unset = UNSET
    status: Literal["cancelled"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        run_id = self.run_id

        thread_id = self.thread_id

        schema_version = self.schema_version

        status = self.status

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "runId": run_id,
                "threadId": thread_id,
            }
        )
        if schema_version is not UNSET:
            field_dict["schema_version"] = schema_version
        if status is not UNSET:
            field_dict["status"] = status

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        run_id = d.pop("runId")

        thread_id = d.pop("threadId")

        schema_version = cast(Literal["1"] | Unset, d.pop("schema_version", UNSET))
        if schema_version != "1" and not isinstance(schema_version, Unset):
            raise ValueError(f"schema_version must match const '1', got '{schema_version}'")

        status = cast(Literal["cancelled"] | Unset, d.pop("status", UNSET))
        if status != "cancelled" and not isinstance(status, Unset):
            raise ValueError(f"status must match const 'cancelled', got '{status}'")

        hosted_agui_cancel_receipt = cls(
            run_id=run_id,
            thread_id=thread_id,
            schema_version=schema_version,
            status=status,
        )

        return hosted_agui_cancel_receipt
