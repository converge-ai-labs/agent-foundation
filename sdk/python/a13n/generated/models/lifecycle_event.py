from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.lifecycle_entity_type import LifecycleEntityType
from ..models.lifecycle_projection_state import LifecycleProjectionState
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.lifecycle_event_payload import LifecycleEventPayload
    from ..models.safe_failure import SafeFailure


T = TypeVar("T", bound="LifecycleEvent")


@_attrs_define(repr=False)
class LifecycleEvent:
    """
    Attributes:
        actor_type (str):
        created_at (datetime.datetime):
        entity_id (str):
        entity_type (LifecycleEntityType):
        entity_version (int):
        event_type (str):
        id (str):
        mutation_id (str):
        occurred_at (datetime.datetime):
        organization_id (str):
        payload (LifecycleEventPayload):
        projection_attempts (int):
        projection_state (LifecycleProjectionState):
        resource_seq (int):
        run_id (str):
        schema_version (str):
        seq (int):
        actor_id (None | str | Unset):
        projected_at (datetime.datetime | None | Unset):
        projection_error (None | SafeFailure | Unset):
        projection_lease_expires_at (datetime.datetime | None | Unset):
        projection_next_attempt_at (datetime.datetime | None | Unset):
        run_attempt_id (None | str | Unset):
        session_id (None | str | Unset):
        thread_id (None | str | Unset):
    """

    actor_type: str
    created_at: datetime.datetime
    entity_id: str
    entity_type: LifecycleEntityType
    entity_version: int
    event_type: str
    id: str
    mutation_id: str
    occurred_at: datetime.datetime
    organization_id: str
    payload: LifecycleEventPayload
    projection_attempts: int
    projection_state: LifecycleProjectionState
    resource_seq: int
    run_id: str
    schema_version: str
    seq: int
    actor_id: str | Unset | None = UNSET
    projected_at: datetime.datetime | Unset | None = UNSET
    projection_error: SafeFailure | Unset | None = UNSET
    projection_lease_expires_at: datetime.datetime | Unset | None = UNSET
    projection_next_attempt_at: datetime.datetime | Unset | None = UNSET
    run_attempt_id: str | Unset | None = UNSET
    session_id: str | Unset | None = UNSET
    thread_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.safe_failure import SafeFailure

        actor_type = self.actor_type

        created_at = self.created_at.isoformat()

        entity_id = self.entity_id

        entity_type = self.entity_type.value

        entity_version = self.entity_version

        event_type = self.event_type

        id = self.id

        mutation_id = self.mutation_id

        occurred_at = self.occurred_at.isoformat()

        organization_id = self.organization_id

        payload = self.payload.to_dict()

        projection_attempts = self.projection_attempts

        projection_state = self.projection_state.value

        resource_seq = self.resource_seq

        run_id = self.run_id

        schema_version = self.schema_version

        seq = self.seq

        actor_id: str | Unset | None
        if isinstance(self.actor_id, Unset):
            actor_id = UNSET
        else:
            actor_id = self.actor_id

        projected_at: str | Unset | None
        if isinstance(self.projected_at, Unset):
            projected_at = UNSET
        elif isinstance(self.projected_at, datetime.datetime):
            projected_at = self.projected_at.isoformat()
        else:
            projected_at = self.projected_at

        projection_error: dict[str, Any] | Unset | None
        if isinstance(self.projection_error, Unset):
            projection_error = UNSET
        elif isinstance(self.projection_error, SafeFailure):
            projection_error = self.projection_error.to_dict()
        else:
            projection_error = self.projection_error

        projection_lease_expires_at: str | Unset | None
        if isinstance(self.projection_lease_expires_at, Unset):
            projection_lease_expires_at = UNSET
        elif isinstance(self.projection_lease_expires_at, datetime.datetime):
            projection_lease_expires_at = self.projection_lease_expires_at.isoformat()
        else:
            projection_lease_expires_at = self.projection_lease_expires_at

        projection_next_attempt_at: str | Unset | None
        if isinstance(self.projection_next_attempt_at, Unset):
            projection_next_attempt_at = UNSET
        elif isinstance(self.projection_next_attempt_at, datetime.datetime):
            projection_next_attempt_at = self.projection_next_attempt_at.isoformat()
        else:
            projection_next_attempt_at = self.projection_next_attempt_at

        run_attempt_id: str | Unset | None
        if isinstance(self.run_attempt_id, Unset):
            run_attempt_id = UNSET
        else:
            run_attempt_id = self.run_attempt_id

        session_id: str | Unset | None
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        thread_id: str | Unset | None
        if isinstance(self.thread_id, Unset):
            thread_id = UNSET
        else:
            thread_id = self.thread_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "actor_type": actor_type,
                "created_at": created_at,
                "entity_id": entity_id,
                "entity_type": entity_type,
                "entity_version": entity_version,
                "event_type": event_type,
                "id": id,
                "mutation_id": mutation_id,
                "occurred_at": occurred_at,
                "organization_id": organization_id,
                "payload": payload,
                "projection_attempts": projection_attempts,
                "projection_state": projection_state,
                "resource_seq": resource_seq,
                "run_id": run_id,
                "schema_version": schema_version,
                "seq": seq,
            }
        )
        if actor_id is not UNSET:
            field_dict["actor_id"] = actor_id
        if projected_at is not UNSET:
            field_dict["projected_at"] = projected_at
        if projection_error is not UNSET:
            field_dict["projection_error"] = projection_error
        if projection_lease_expires_at is not UNSET:
            field_dict["projection_lease_expires_at"] = projection_lease_expires_at
        if projection_next_attempt_at is not UNSET:
            field_dict["projection_next_attempt_at"] = projection_next_attempt_at
        if run_attempt_id is not UNSET:
            field_dict["run_attempt_id"] = run_attempt_id
        if session_id is not UNSET:
            field_dict["session_id"] = session_id
        if thread_id is not UNSET:
            field_dict["thread_id"] = thread_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.lifecycle_event_payload import LifecycleEventPayload
        from ..models.safe_failure import SafeFailure

        d = dict(src_dict)
        actor_type = d.pop("actor_type")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        entity_id = d.pop("entity_id")

        entity_type = LifecycleEntityType(d.pop("entity_type"))

        entity_version = d.pop("entity_version")

        event_type = d.pop("event_type")

        id = d.pop("id")

        mutation_id = d.pop("mutation_id")

        occurred_at = datetime.datetime.fromisoformat(d.pop("occurred_at"))

        organization_id = d.pop("organization_id")

        payload = LifecycleEventPayload.from_dict(d.pop("payload"))

        projection_attempts = d.pop("projection_attempts")

        projection_state = LifecycleProjectionState(d.pop("projection_state"))

        resource_seq = d.pop("resource_seq")

        run_id = d.pop("run_id")

        schema_version = d.pop("schema_version")

        seq = d.pop("seq")

        def _parse_actor_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        actor_id = _parse_actor_id(d.pop("actor_id", UNSET))

        def _parse_projected_at(data: object) -> datetime.datetime | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                projected_at_type_0 = datetime.datetime.fromisoformat(data)

                return projected_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | Unset | None, data)

        projected_at = _parse_projected_at(d.pop("projected_at", UNSET))

        def _parse_projection_error(data: object) -> SafeFailure | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                projection_error_type_0 = SafeFailure.from_dict(data)

                return projection_error_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(SafeFailure | Unset | None, data)

        projection_error = _parse_projection_error(d.pop("projection_error", UNSET))

        def _parse_projection_lease_expires_at(data: object) -> datetime.datetime | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                projection_lease_expires_at_type_0 = datetime.datetime.fromisoformat(data)

                return projection_lease_expires_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | Unset | None, data)

        projection_lease_expires_at = _parse_projection_lease_expires_at(d.pop("projection_lease_expires_at", UNSET))

        def _parse_projection_next_attempt_at(data: object) -> datetime.datetime | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                projection_next_attempt_at_type_0 = datetime.datetime.fromisoformat(data)

                return projection_next_attempt_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | Unset | None, data)

        projection_next_attempt_at = _parse_projection_next_attempt_at(d.pop("projection_next_attempt_at", UNSET))

        def _parse_run_attempt_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        run_attempt_id = _parse_run_attempt_id(d.pop("run_attempt_id", UNSET))

        def _parse_session_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        def _parse_thread_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        thread_id = _parse_thread_id(d.pop("thread_id", UNSET))

        lifecycle_event = cls(
            actor_type=actor_type,
            created_at=created_at,
            entity_id=entity_id,
            entity_type=entity_type,
            entity_version=entity_version,
            event_type=event_type,
            id=id,
            mutation_id=mutation_id,
            occurred_at=occurred_at,
            organization_id=organization_id,
            payload=payload,
            projection_attempts=projection_attempts,
            projection_state=projection_state,
            resource_seq=resource_seq,
            run_id=run_id,
            schema_version=schema_version,
            seq=seq,
            actor_id=actor_id,
            projected_at=projected_at,
            projection_error=projection_error,
            projection_lease_expires_at=projection_lease_expires_at,
            projection_next_attempt_at=projection_next_attempt_at,
            run_attempt_id=run_attempt_id,
            session_id=session_id,
            thread_id=thread_id,
        )

        return lifecycle_event
