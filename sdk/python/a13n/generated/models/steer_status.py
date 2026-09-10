from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..models.steer_status_status import SteerStatusStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="SteerStatus")


@_attrs_define(repr=False)
class SteerStatus:
    """
    Attributes:
        accepted_against_run_id (str):
        consumed_by_run_id (None | str):
        consumed_checkpoint_seq (int | None):
        consumed_state_digest_sha256 (None | str):
        created_at (datetime.datetime):
        delivery_sequence (int):
        finalized_at (datetime.datetime | None):
        session_id (str):
        source_waiting_run_id (None | str):
        status (SteerStatusStatus):
        steer_id (str):
        target_run_id (None | str):
        thread_id (str):
        schema_version (Literal['1'] | Unset):
    """

    accepted_against_run_id: str
    consumed_by_run_id: str | None
    consumed_checkpoint_seq: int | None
    consumed_state_digest_sha256: str | None
    created_at: datetime.datetime
    delivery_sequence: int
    finalized_at: datetime.datetime | None
    session_id: str
    source_waiting_run_id: str | None
    status: SteerStatusStatus
    steer_id: str
    target_run_id: str | None
    thread_id: str
    schema_version: Literal["1"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        accepted_against_run_id = self.accepted_against_run_id

        consumed_by_run_id: str | None
        consumed_by_run_id = self.consumed_by_run_id

        consumed_checkpoint_seq: int | None
        consumed_checkpoint_seq = self.consumed_checkpoint_seq

        consumed_state_digest_sha256: str | None
        consumed_state_digest_sha256 = self.consumed_state_digest_sha256

        created_at = self.created_at.isoformat()

        delivery_sequence = self.delivery_sequence

        finalized_at: str | None
        if isinstance(self.finalized_at, datetime.datetime):
            finalized_at = self.finalized_at.isoformat()
        else:
            finalized_at = self.finalized_at

        session_id = self.session_id

        source_waiting_run_id: str | None
        source_waiting_run_id = self.source_waiting_run_id

        status = self.status.value

        steer_id = self.steer_id

        target_run_id: str | None
        target_run_id = self.target_run_id

        thread_id = self.thread_id

        schema_version = self.schema_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "accepted_against_run_id": accepted_against_run_id,
                "consumed_by_run_id": consumed_by_run_id,
                "consumed_checkpoint_seq": consumed_checkpoint_seq,
                "consumed_state_digest_sha256": consumed_state_digest_sha256,
                "created_at": created_at,
                "delivery_sequence": delivery_sequence,
                "finalized_at": finalized_at,
                "session_id": session_id,
                "source_waiting_run_id": source_waiting_run_id,
                "status": status,
                "steer_id": steer_id,
                "target_run_id": target_run_id,
                "thread_id": thread_id,
            }
        )
        if schema_version is not UNSET:
            field_dict["schema_version"] = schema_version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        accepted_against_run_id = d.pop("accepted_against_run_id")

        def _parse_consumed_by_run_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        consumed_by_run_id = _parse_consumed_by_run_id(d.pop("consumed_by_run_id"))

        def _parse_consumed_checkpoint_seq(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        consumed_checkpoint_seq = _parse_consumed_checkpoint_seq(d.pop("consumed_checkpoint_seq"))

        def _parse_consumed_state_digest_sha256(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        consumed_state_digest_sha256 = _parse_consumed_state_digest_sha256(d.pop("consumed_state_digest_sha256"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        delivery_sequence = d.pop("delivery_sequence")

        def _parse_finalized_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                finalized_at_type_0 = datetime.datetime.fromisoformat(data)

                return finalized_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        finalized_at = _parse_finalized_at(d.pop("finalized_at"))

        session_id = d.pop("session_id")

        def _parse_source_waiting_run_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        source_waiting_run_id = _parse_source_waiting_run_id(d.pop("source_waiting_run_id"))

        status = SteerStatusStatus(d.pop("status"))

        steer_id = d.pop("steer_id")

        def _parse_target_run_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        target_run_id = _parse_target_run_id(d.pop("target_run_id"))

        thread_id = d.pop("thread_id")

        schema_version = cast(Literal["1"] | Unset, d.pop("schema_version", UNSET))
        if schema_version != "1" and not isinstance(schema_version, Unset):
            raise ValueError(f"schema_version must match const '1', got '{schema_version}'")

        steer_status = cls(
            accepted_against_run_id=accepted_against_run_id,
            consumed_by_run_id=consumed_by_run_id,
            consumed_checkpoint_seq=consumed_checkpoint_seq,
            consumed_state_digest_sha256=consumed_state_digest_sha256,
            created_at=created_at,
            delivery_sequence=delivery_sequence,
            finalized_at=finalized_at,
            session_id=session_id,
            source_waiting_run_id=source_waiting_run_id,
            status=status,
            steer_id=steer_id,
            target_run_id=target_run_id,
            thread_id=thread_id,
            schema_version=schema_version,
        )

        return steer_status
