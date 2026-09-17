"""Validated, resumable semantic display checkpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from a13n_service.temporal import UtcDateTime

from .domain import JsonObject, RedisStreamId, ResourceId, RetainedItem, RunStreamError


def stream_position(value: str) -> tuple[int, int]:
    milliseconds, sequence = value.split("-", maxsplit=1)
    return int(milliseconds), int(sequence)


class DisplayIntegrityError(RunStreamError):
    """Display content cannot be trusted as a consumption checkpoint."""


class DisplayLimitExceeded(RunStreamError):
    """A projection exceeded its configured durable content bound."""


class RunDisplaySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    projection_schema_version: Literal["1"] = "1"
    version: int = Field(ge=1)
    run_id: ResourceId
    thread_id: ResourceId
    stream_key_digest_sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    cursor: RedisStreamId | None
    complete: bool = True
    incomplete_reason: str | None = Field(default=None, min_length=1, max_length=256)
    finalized: bool = False
    closed_at: UtcDateTime | None = None
    source_run_attempt_ids: tuple[ResourceId, ...] = ()
    items: tuple[RetainedItem, ...] = ()
    # Item content contains all accumulators; this envelope versions continuation.
    merge_state: JsonObject = Field(default_factory=lambda: {"version": "1"})

    @model_validator(mode="after")
    def validate_checkpoint(self) -> RunDisplaySnapshot:
        if self.merge_state != {"version": "1"}:
            raise ValueError("unsupported display merge state")
        if self.complete != (self.incomplete_reason is None):
            raise ValueError("display coverage and reason disagree")
        if self.finalized != (self.closed_at is not None):
            raise ValueError("display finalization and closure disagree")
        if len(set(self.source_run_attempt_ids)) != len(self.source_run_attempt_ids):
            raise ValueError("duplicate display Attempt identity")
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("duplicate display Item identity")
        previous = (0, 0)
        for item in self.items:
            first, last = stream_position(item.first_stream_id), stream_position(item.last_stream_id)
            if self.cursor is None or not previous < first <= last <= stream_position(self.cursor):
                raise ValueError("display Item positions are outside ordered coverage")
            if self.finalized and item.state == "in_progress":
                raise ValueError("finalized display contains an open Item")
            previous = first
        return self


def validate_successor(previous: RunDisplaySnapshot | None, candidate: RunDisplaySnapshot) -> None:
    if previous is None:
        if candidate.version != 1:
            raise DisplayIntegrityError("first display publication must have version one")
        return
    if previous.finalized:
        raise DisplayIntegrityError("finalized display cannot be replaced")
    if candidate.version != previous.version + 1:
        raise DisplayIntegrityError("display publication version must advance once")
    if (candidate.run_id, candidate.thread_id, candidate.stream_key_digest_sha256) != (
        previous.run_id,
        previous.thread_id,
        previous.stream_key_digest_sha256,
    ):
        raise DisplayIntegrityError("display successor changed source identity")
    if previous.cursor is not None and (
        candidate.cursor is None or stream_position(candidate.cursor) < stream_position(previous.cursor)
    ):
        raise DisplayIntegrityError("display cursor cannot regress")
    if not previous.complete and candidate.complete:
        raise DisplayIntegrityError("missing source coverage cannot become complete")
    if not previous.complete and candidate.cursor != previous.cursor:
        raise DisplayIntegrityError("display cursor cannot advance across missing coverage")
    if candidate.source_run_attempt_ids[: len(previous.source_run_attempt_ids)] != previous.source_run_attempt_ids:
        raise DisplayIntegrityError("display successor discarded source Attempt attribution")
    if len(candidate.items) < len(previous.items):
        raise DisplayIntegrityError("display successor discarded committed Items")
    for before, after in zip(previous.items, candidate.items, strict=False):
        if (before.id, before.kind, before.parent_item_id, before.first_stream_id) != (
            after.id,
            after.kind,
            after.parent_item_id,
            after.first_stream_id,
        ) or stream_position(after.last_stream_id) < stream_position(before.last_stream_id):
            raise DisplayIntegrityError("display successor changed committed Item identity or order")
