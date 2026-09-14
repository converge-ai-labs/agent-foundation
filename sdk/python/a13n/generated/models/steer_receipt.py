from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="SteerReceipt")


@_attrs_define(repr=False)
class SteerReceipt:
    """
    Attributes:
        accepted_at (datetime.datetime):
        delivery_sequence (int):
        run_id (str):
        session_id (str):
        steer_id (str):
        thread_id (str):
        schema_version (Literal['1'] | Unset):
    """

    accepted_at: datetime.datetime
    delivery_sequence: int
    run_id: str
    session_id: str
    steer_id: str
    thread_id: str
    schema_version: Literal["1"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        accepted_at = self.accepted_at.isoformat()

        delivery_sequence = self.delivery_sequence

        run_id = self.run_id

        session_id = self.session_id

        steer_id = self.steer_id

        thread_id = self.thread_id

        schema_version = self.schema_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "accepted_at": accepted_at,
                "delivery_sequence": delivery_sequence,
                "run_id": run_id,
                "session_id": session_id,
                "steer_id": steer_id,
                "thread_id": thread_id,
            }
        )
        if schema_version is not UNSET:
            field_dict["schema_version"] = schema_version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        accepted_at = datetime.datetime.fromisoformat(d.pop("accepted_at"))

        delivery_sequence = d.pop("delivery_sequence")

        run_id = d.pop("run_id")

        session_id = d.pop("session_id")

        steer_id = d.pop("steer_id")

        thread_id = d.pop("thread_id")

        schema_version = cast(Literal["1"] | Unset, d.pop("schema_version", UNSET))
        if schema_version != "1" and not isinstance(schema_version, Unset):
            raise ValueError(f"schema_version must match const '1', got '{schema_version}'")

        steer_receipt = cls(
            accepted_at=accepted_at,
            delivery_sequence=delivery_sequence,
            run_id=run_id,
            session_id=session_id,
            steer_id=steer_id,
            thread_id=thread_id,
            schema_version=schema_version,
        )

        return steer_receipt
