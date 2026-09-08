"""Canonical lifecycle facts emitted by interaction state transitions."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal

import rfc8785
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.lifecycle import (
    LifecycleEntityType,
    LifecycleEventDraft,
    append_lifecycle_event,
    new_lifecycle_event_id,
    new_mutation_id,
)
from a13n_service.lifecycle.models import LifecycleEventRecord

from .models import RunAttemptRecord, RunRecord

RunEventType = Literal[
    "run.accepted",
    "run.running",
    "run.waiting",
    "run.completed",
    "run.failed",
    "run.cancelled",
]
RunAttemptEventType = Literal[
    "run_attempt.leased",
    "run_attempt.running",
    "run_attempt.succeeded",
    "run_attempt.yielded",
    "run_attempt.failed",
    "run_attempt.cancelled",
]


DeliveryIntentWriter = Callable[[AsyncSession, LifecycleEventRecord], Awaitable[object]]


class LifecycleWriter:
    """Write facts and configured delivery intents in the caller's transaction.

    Writers only perform relational work. A failed writer rolls back the fact
    and authoritative mutation along with every other required delivery intent.
    """

    def __init__(self, delivery_writers: tuple[DeliveryIntentWriter, ...] = ()) -> None:
        self._delivery_writers = delivery_writers

    async def append_accepted_run_lifecycle(
        self,
        database: AsyncSession,
        run: RunRecord,
        *,
        mutation_id: str | None = None,
    ) -> str:
        """Record acceptance with the exact authority captured by the Run."""

        authority = run.to_resource().authority_principal
        return await self.append_run_lifecycle(
            database,
            run,
            "run.accepted",
            mutation_id=new_mutation_id() if mutation_id is None else mutation_id,
            occurred_at=run.created_at,
            actor_type=authority.principal_type.value,
            actor_id=authority.principal_id,
        )

    async def append_run_with_attempt_lifecycle(
        self,
        database: AsyncSession,
        run: RunRecord,
        run_event_type: RunEventType,
        *,
        attempt: RunAttemptRecord,
        attempt_event_type: RunAttemptEventType,
        occurred_at: datetime,
        actor_type: str,
        actor_id: str | None,
        mutation_id: str | None = None,
    ) -> str:
        """Record one Run transition and its correlated Attempt transition."""

        resolved_mutation_id = new_mutation_id() if mutation_id is None else mutation_id
        run_event_id = new_lifecycle_event_id()
        await self.append_run_attempt_lifecycle(
            database,
            run,
            attempt,
            attempt_event_type,
            mutation_id=resolved_mutation_id,
            occurred_at=occurred_at,
            resulting_run_lifecycle_event_id=run_event_id,
        )
        return await self.append_run_lifecycle(
            database,
            run,
            run_event_type,
            event_id=run_event_id,
            mutation_id=resolved_mutation_id,
            occurred_at=occurred_at,
            actor_type=actor_type,
            actor_id=actor_id,
            final_run_attempt_id=attempt.id,
        )

    async def append_run_lifecycle(
        self,
        database: AsyncSession,
        run: RunRecord,
        event_type: RunEventType,
        *,
        event_id: str | None = None,
        mutation_id: str | None = None,
        occurred_at: datetime,
        actor_type: str,
        actor_id: str | None,
        final_run_attempt_id: str | None = None,
    ) -> str:
        record = await self._append_lifecycle_with_hooks(
            database,
            LifecycleEventDraft(
                id=new_lifecycle_event_id() if event_id is None else event_id,
                organization_id=run.organization_id,
                entity_type=LifecycleEntityType.run,
                entity_id=run.id,
                entity_version=run.version,
                event_type=event_type,
                mutation_id=new_mutation_id() if mutation_id is None else mutation_id,
                session_id=run.session_id,
                thread_id=run.thread_id,
                run_id=run.id,
                payload=_run_payload(run, event_type, final_run_attempt_id=final_run_attempt_id),
                actor_type=actor_type,
                actor_id=actor_id,
                occurred_at=occurred_at,
            ),
        )
        return record.id

    async def append_run_attempt_lifecycle(
        self,
        database: AsyncSession,
        run: RunRecord,
        attempt: RunAttemptRecord,
        event_type: RunAttemptEventType,
        *,
        mutation_id: str,
        occurred_at: datetime,
        resulting_run_lifecycle_event_id: str | None = None,
    ) -> str:
        record = await self._append_lifecycle_with_hooks(
            database,
            LifecycleEventDraft(
                organization_id=run.organization_id,
                entity_type=LifecycleEntityType.run_attempt,
                entity_id=attempt.id,
                entity_version=attempt.version,
                event_type=event_type,
                mutation_id=mutation_id,
                session_id=run.session_id,
                thread_id=run.thread_id,
                run_id=run.id,
                run_attempt_id=attempt.id,
                payload=_attempt_payload(
                    attempt,
                    event_type,
                    resulting_run_lifecycle_event_id=resulting_run_lifecycle_event_id,
                ),
                actor_type="worker",
                actor_id=attempt.worker_id,
                occurred_at=occurred_at,
            ),
        )
        return record.id

    async def _append_lifecycle_with_hooks(
        self,
        database: AsyncSession,
        draft: LifecycleEventDraft,
    ) -> LifecycleEventRecord:
        record = await append_lifecycle_event(database, draft)
        for writer in self._delivery_writers:
            await writer(database, record)
        return record


def _run_payload(
    run: RunRecord,
    event_type: RunEventType,
    *,
    final_run_attempt_id: str | None,
) -> dict[str, JsonValue]:
    resource = run.to_resource()
    if event_type == "run.accepted":
        return {
            "status": resource.status.value,
            "parent_run_id": resource.parent_run_id,
            "retry_of_run_id": resource.retry_of_run_id,
            "lineage_kind": resource.lineage_kind.value,
            "trigger_type": resource.trigger_type,
            "trigger_entity_type": resource.trigger_entity_type,
            "trigger_entity_id": resource.trigger_entity_id,
            "agent_id": resource.agent_id,
            "agent_revision_id": resource.agent_revision_id,
            "effective_agent_config_digest": resource.effective_agent_config_digest,
            "available_at": resource.available_at.isoformat(),
        }
    if event_type == "run.running":
        return {
            "status": resource.status.value,
            "current_run_attempt_id": resource.current_run_attempt_id,
            "started_at": None if resource.started_at is None else resource.started_at.isoformat(),
            "model_execution_observation": resource.model_execution_observation.model_dump(mode="json"),
        }
    if event_type == "run.waiting":
        return {
            "status": resource.status.value,
            "wait_reason": None if resource.wait_reason is None else resource.wait_reason.value,
            "pending": None if resource.pending is None else resource.pending.model_dump(mode="json"),
            "sealed_at": None if resource.sealed_at is None else resource.sealed_at.isoformat(),
        }
    if event_type == "run.completed":
        payload: dict[str, JsonValue] = {
            "status": resource.status.value,
            "final_run_attempt_id": final_run_attempt_id,
            "completed_at": None if resource.completed_at is None else resource.completed_at.isoformat(),
            "sealed_at": None if resource.sealed_at is None else resource.sealed_at.isoformat(),
            "usage": resource.usage_charged.model_dump(mode="json"),
        }
        if resource.output_object is not None:
            payload["output_object"] = resource.output_object.model_dump(mode="json")
        elif "output" in resource.model_fields_set:
            payload.update(_bounded_value("output", resource.output))
        if resource.output_text is not None:
            payload.update(_bounded_value("output_text", resource.output_text))
        return payload
    return {
        "status": resource.status.value,
        "final_run_attempt_id": final_run_attempt_id,
        "failure": None if resource.failure is None else resource.failure.model_dump(mode="json", by_alias=True),
        "sealed_at": None if resource.sealed_at is None else resource.sealed_at.isoformat(),
    }


def _attempt_payload(
    attempt: RunAttemptRecord,
    event_type: RunAttemptEventType,
    *,
    resulting_run_lifecycle_event_id: str | None,
) -> dict[str, JsonValue]:
    resource = attempt.to_resource()
    payload: dict[str, JsonValue] = {
        "status": resource.status.value,
        "attempt_number": resource.attempt_number,
        "worker_id": resource.worker_id,
        "worker_build_id": resource.worker_build_id,
        "replaces_run_attempt_id": resource.replaces_run_attempt_id,
        "start_reason": resource.start_reason,
    }
    if event_type == "run_attempt.leased":
        payload.update(
            lease_expires_at=resource.lease_expires_at.isoformat(),
            created_at=resource.created_at.isoformat(),
        )
    elif event_type == "run_attempt.running":
        payload.update(
            harness_run_id=resource.harness_run_id,
            started_at=None if resource.started_at is None else resource.started_at.isoformat(),
            model_execution_observation=resource.model_execution_observation.model_dump(mode="json"),
        )
    else:
        payload.update(
            usage=resource.usage.model_dump(mode="json"),
            finished_at=None if resource.finished_at is None else resource.finished_at.isoformat(),
        )
        if resource.failure is not None:
            payload["failure"] = resource.failure.model_dump(mode="json", by_alias=True)
        if resource.yield_reason is not None:
            payload["yield_reason"] = resource.yield_reason.value
        if resulting_run_lifecycle_event_id is not None:
            payload["resulting_run_lifecycle_event_id"] = resulting_run_lifecycle_event_id
    return payload


def _bounded_value(name: str, value: JsonValue) -> dict[str, JsonValue]:
    encoded = rfc8785.dumps(value)
    if len(encoded) <= 16 * 1024:
        return {name: value}
    return {
        f"{name}_omitted": True,
        f"{name}_digest_sha256": hashlib.sha256(encoded).hexdigest(),
        f"{name}_size_bytes": len(encoded),
    }
