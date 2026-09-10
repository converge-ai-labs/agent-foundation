from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="InterruptReceipt")


@_attrs_define(repr=False)
class InterruptReceipt:
    """
    Attributes:
        interrupted_at (datetime.datetime):
        run_id (str):
        schema_version (Literal['1'] | Unset):
        status (Literal['cancelled'] | Unset):
    """

    interrupted_at: datetime.datetime
    run_id: str
    schema_version: Literal["1"] | Unset = UNSET
    status: Literal["cancelled"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        interrupted_at = self.interrupted_at.isoformat()

        run_id = self.run_id

        schema_version = self.schema_version

        status = self.status

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "interrupted_at": interrupted_at,
                "run_id": run_id,
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
        interrupted_at = datetime.datetime.fromisoformat(d.pop("interrupted_at"))

        run_id = d.pop("run_id")

        schema_version = cast(Literal["1"] | Unset, d.pop("schema_version", UNSET))
        if schema_version != "1" and not isinstance(schema_version, Unset):
            raise ValueError(f"schema_version must match const '1', got '{schema_version}'")

        status = cast(Literal["cancelled"] | Unset, d.pop("status", UNSET))
        if status != "cancelled" and not isinstance(status, Unset):
            raise ValueError(f"status must match const 'cancelled', got '{status}'")

        interrupt_receipt = cls(
            interrupted_at=interrupted_at,
            run_id=run_id,
            schema_version=schema_version,
            status=status,
        )

        return interrupt_receipt
