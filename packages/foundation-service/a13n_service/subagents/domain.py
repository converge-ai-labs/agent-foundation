"""Durable values for asynchronous child Runs and result delivery."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

import rfc8785
from pydantic import Field, JsonValue, StringConstraints, field_validator, model_validator

from a13n_service.ids import new_object_id
from a13n_service.interactions.domain import (
    ObjectId,
    Run,
    Sha256Digest,
    StrictModel,
    ThreadId,
    UtcDateTime,
)

MAX_INLINE_ASYNC_RESULT_BYTES = 256 * 1024
SubagentName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,62}$", max_length=63)]


class ChildCancellationPolicy(StrEnum):
    independent = "independent"
    request_child_cancel = "request_child_cancel"


class ChildResultVisibility(StrEnum):
    parent_thread = "parent_thread"
    session = "session"


class ChildRunRelationship(StrictModel):
    id: ObjectId
    parent_run_id: ObjectId
    parent_run_attempt_id: ObjectId
    parent_run_attempt_generation: int = Field(ge=1)
    subagent_name: SubagentName
    child_run_id: ObjectId
    child_thread_id: ThreadId
    cancellation_policy: ChildCancellationPolicy
    result_visibility: ChildResultVisibility
    created_at: UtcDateTime

    @model_validator(mode="after")
    def child_is_distinct(self) -> ChildRunRelationship:
        if self.parent_run_id == self.child_run_id:
            raise ValueError("asynchronous child Run must be distinct from its parent Run")
        return self


def child_relationship_is_visible(
    relationship: ChildRunRelationship,
    *,
    origin_parent: Run,
    requesting_parent: Run,
) -> bool:
    """Apply the retained child relationship's Thread-or-Session read boundary."""

    return (
        relationship.parent_run_id == origin_parent.id
        and origin_parent.tenant_id == requesting_parent.tenant_id
        and origin_parent.session_id == requesting_parent.session_id
        and (
            origin_parent.thread_id == requesting_parent.thread_id
            or relationship.result_visibility is ChildResultVisibility.session
        )
    )


class AsyncSubagentResultInboxPayload(StrictModel):
    schema_version: Literal["1"] = "1"
    relationship_id: ObjectId
    subagent_name: SubagentName
    child_thread_id: ThreadId
    child_run_id: ObjectId
    terminal_status: Literal["completed", "failed", "cancelled"]
    terminal_result_item_id: ObjectId | None = None
    result_payload: JsonValue | None = None
    result_digest: Sha256Digest | None = None

    @field_validator("result_payload")
    @classmethod
    def payload_is_bounded_canonical_json(cls, value: JsonValue | None) -> JsonValue | None:
        try:
            encoded = rfc8785.dumps(value)
        except rfc8785.CanonicalizationError as error:
            raise ValueError("asynchronous child result payload must be canonical JSON") from error
        if len(encoded) > MAX_INLINE_ASYNC_RESULT_BYTES:
            raise ValueError("asynchronous child result payload exceeds the inline limit")
        return value

    @model_validator(mode="after")
    def result_representation_matches_terminal_status(self) -> AsyncSubagentResultInboxPayload:
        inline = "result_payload" in self.model_fields_set
        referenced = self.terminal_result_item_id is not None
        if self.terminal_status == "completed":
            if inline == referenced:
                raise ValueError("completed asynchronous result requires exactly one result representation")
        elif not inline or self.result_payload is None or referenced:
            raise ValueError("unsuccessful asynchronous result requires one inline failure representation")
        return self

    def as_json(self) -> dict[str, JsonValue]:
        value = self.model_dump(mode="json", by_alias=True)
        if "result_payload" not in self.model_fields_set:
            value.pop("result_payload", None)
        if self.terminal_result_item_id is None:
            value.pop("terminal_result_item_id", None)
        return value

    def canonical_bytes(self) -> bytes:
        try:
            return rfc8785.dumps(self.as_json())
        except rfc8785.CanonicalizationError as error:  # pragma: no cover - validated fields make this defensive
            raise ValueError("asynchronous child result payload is not canonicalizable") from error


def new_child_run_relationship_id() -> str:
    return new_object_id("crr")


__all__ = [
    "MAX_INLINE_ASYNC_RESULT_BYTES",
    "AsyncSubagentResultInboxPayload",
    "ChildCancellationPolicy",
    "ChildResultVisibility",
    "ChildRunRelationship",
    "child_relationship_is_visible",
    "new_child_run_relationship_id",
]
