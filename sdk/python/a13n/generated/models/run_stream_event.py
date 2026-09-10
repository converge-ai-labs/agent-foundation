from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.run_stream_event_payload import RunStreamEventPayload


T = TypeVar("T", bound="RunStreamEvent")


@_attrs_define(repr=False)
class RunStreamEvent:
    """
    Attributes:
        event_id (str):
        event_type (str):
        occurred_at (datetime.datetime):
        payload (RunStreamEventPayload):
        run_id (str):
        thread_id (str):
        harness_run_id (None | str | Unset):
        item_id (None | str | Unset):
        lifecycle_event_id (None | str | Unset):
        run_attempt_id (None | str | Unset):
        schema_version (Literal['1'] | Unset):
    """

    event_id: str
    event_type: str
    occurred_at: datetime.datetime
    payload: RunStreamEventPayload
    run_id: str
    thread_id: str
    harness_run_id: str | Unset | None = UNSET
    item_id: str | Unset | None = UNSET
    lifecycle_event_id: str | Unset | None = UNSET
    run_attempt_id: str | Unset | None = UNSET
    schema_version: Literal["1"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        event_id = self.event_id

        event_type = self.event_type

        occurred_at = self.occurred_at.isoformat()

        payload = self.payload.to_dict()

        run_id = self.run_id

        thread_id = self.thread_id

        harness_run_id: str | Unset | None
        if isinstance(self.harness_run_id, Unset):
            harness_run_id = UNSET
        else:
            harness_run_id = self.harness_run_id

        item_id: str | Unset | None
        if isinstance(self.item_id, Unset):
            item_id = UNSET
        else:
            item_id = self.item_id

        lifecycle_event_id: str | Unset | None
        if isinstance(self.lifecycle_event_id, Unset):
            lifecycle_event_id = UNSET
        else:
            lifecycle_event_id = self.lifecycle_event_id

        run_attempt_id: str | Unset | None
        if isinstance(self.run_attempt_id, Unset):
            run_attempt_id = UNSET
        else:
            run_attempt_id = self.run_attempt_id

        schema_version = self.schema_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "event_id": event_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "payload": payload,
                "run_id": run_id,
                "thread_id": thread_id,
            }
        )
        if harness_run_id is not UNSET:
            field_dict["harness_run_id"] = harness_run_id
        if item_id is not UNSET:
            field_dict["item_id"] = item_id
        if lifecycle_event_id is not UNSET:
            field_dict["lifecycle_event_id"] = lifecycle_event_id
        if run_attempt_id is not UNSET:
            field_dict["run_attempt_id"] = run_attempt_id
        if schema_version is not UNSET:
            field_dict["schema_version"] = schema_version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.run_stream_event_payload import RunStreamEventPayload

        d = dict(src_dict)
        event_id = d.pop("event_id")

        event_type = d.pop("event_type")

        occurred_at = datetime.datetime.fromisoformat(d.pop("occurred_at"))

        payload = RunStreamEventPayload.from_dict(d.pop("payload"))

        run_id = d.pop("run_id")

        thread_id = d.pop("thread_id")

        def _parse_harness_run_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        harness_run_id = _parse_harness_run_id(d.pop("harness_run_id", UNSET))

        def _parse_item_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        item_id = _parse_item_id(d.pop("item_id", UNSET))

        def _parse_lifecycle_event_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        lifecycle_event_id = _parse_lifecycle_event_id(d.pop("lifecycle_event_id", UNSET))

        def _parse_run_attempt_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        run_attempt_id = _parse_run_attempt_id(d.pop("run_attempt_id", UNSET))

        schema_version = cast(Literal["1"] | Unset, d.pop("schema_version", UNSET))
        if schema_version != "1" and not isinstance(schema_version, Unset):
            raise ValueError(f"schema_version must match const '1', got '{schema_version}'")

        run_stream_event = cls(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=payload,
            run_id=run_id,
            thread_id=thread_id,
            harness_run_id=harness_run_id,
            item_id=item_id,
            lifecycle_event_id=lifecycle_event_id,
            run_attempt_id=run_attempt_id,
            schema_version=schema_version,
        )

        return run_stream_event
