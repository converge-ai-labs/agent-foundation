"""Versioned, bounded messages for Service-owned Environment operation scopes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Literal

from a13n_environment.models import (
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
)
from pydantic import Field, JsonValue, StringConstraints, TypeAdapter, model_validator

from a13n_service.ids import ObjectId

from ..domain import DomainModel
from ..mount_domain import MountName
from .authority import UseIdentity


@dataclass(frozen=True, slots=True)
class RelayLimits:
    request_bytes: int = 65_536
    response_bytes: int = 262_144
    chunk_bytes: int = 65_536
    input_window: int = 8
    retained_requests: int = 512
    pending_bytes: int = 8 * 1024 * 1024
    response_frames: int = 2048
    terminal_reserve: int = 128
    control_requests: int = 16
    control_bytes: int = 4096
    delivery_ms: int = 60_000
    evidence_ms: int = 5_000
    scope_retention_ms: int = 130_000

    def __post_init__(self) -> None:
        if (
            min(
                self.request_bytes,
                self.response_bytes,
                self.chunk_bytes,
                self.input_window,
                self.retained_requests,
                self.pending_bytes,
                self.response_frames,
                self.terminal_reserve,
                self.delivery_ms,
                self.evidence_ms,
            )
            <= 0
        ):
            raise ValueError("Relay bounds must be positive")
        if self.terminal_reserve >= self.response_frames:
            raise ValueError("Relay response capacity must reserve space for terminal outcomes")
        if self.scope_retention_ms <= self.delivery_ms + self.evidence_ms:
            raise ValueError("Relay scope retention must cover delivery and completion evidence")
        if not 0 < self.control_requests < self.retained_requests or self.control_bytes < 1:
            raise ValueError("Relay request capacity must reserve bounded control requests")
        if self.pending_bytes <= self.control_requests * (self.control_bytes * 4 + 2048):
            raise ValueError("Relay pending bytes must reserve room for control requests and outcomes")


DEFAULT_RELAY_LIMITS = RelayLimits()
CONTROL_OPERATIONS = frozenset({"scope.close", "operation.cancel", "transfer.cancel"})


class RelayEnvironmentSnapshot(DomainModel):
    descriptor: EnvironmentDescriptor
    availability: EnvironmentAvailability


class ReadinessRequest(DomainModel):
    operations: frozenset[EnvironmentOperationFamily] = Field(max_length=5)


def operation_permissions(operation: str) -> frozenset[EnvironmentAction]:
    if operation in CONTROL_OPERATIONS or operation in {"scope.describe", "scope.ready"}:
        return frozenset()
    if operation == "file.copy":
        return frozenset({EnvironmentAction.FILE_COPY_SOURCE, EnvironmentAction.FILE_COPY_DESTINATION})
    if operation == "process.rebind":
        return frozenset({EnvironmentAction.PROCESS_INSPECT})
    try:
        return frozenset({EnvironmentAction(f"environment.{operation}")})
    except ValueError:
        raise EnvironmentError("Relay operation is unsupported", code="environment_unsupported") from None


class RelayRequest(DomainModel):
    version: Literal[1] = 1
    request_id: ObjectId
    use: UseIdentity
    operation: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]{1,63}$")]
    mount_name: MountName = "workspace"
    mount_id: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,128}$")] = "mount-prepare"
    deadline_ms: int = Field(gt=0, le=2**53 - 1, strict=True)
    payload: dict[str, JsonValue] = Field(default_factory=dict, repr=False)

    @model_validator(mode="after")
    def _scope(self) -> RelayRequest:
        identity = self.use
        values = (
            identity.use_id,
            identity.run_id,
            identity.attempt_id,
            identity.worker_instance_id,
            identity.connection.organization_id,
            identity.connection.environment_id,
            identity.connection.connection_id,
            identity.connection.connection_epoch,
            identity.connection.owner_instance_id,
        )
        if any(not value or len(value) > 128 or any(character.isspace() for character in value) for value in values):
            raise ValueError("Relay scope identifiers must be bounded and nonblank")
        if type(identity.attempt_fence) is not int or not 1 <= identity.attempt_fence <= 2**63 - 1:
            raise ValueError("Relay requests require a positive Attempt fence")
        return self


class RelayFailure(DomainModel):
    code: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")]
    certainty: Literal["not_dispatched", "complete", "unknown"]
    details: dict[str, JsonValue] = Field(default_factory=dict, repr=False)
    retry_hint: str | None = Field(default=None, max_length=128)

    @classmethod
    def from_environment(cls, error: EnvironmentError) -> RelayFailure:
        safe = error.safe_projection()
        details = TypeAdapter(dict[str, JsonValue]).validate_python(safe["details"])
        stage = details.get("dispatch_stage")
        certainty = "not_dispatched" if stage == "pre_dispatch" else "complete" if stage == "completed" else "unknown"
        return cls.model_validate(
            {"code": safe["code"], "details": details, "retry_hint": safe.get("retry_hint"), "certainty": certainty}
        )


class TransferPosition(DomainModel):
    transfer_id: ObjectId
    sequence: int = Field(ge=0, le=2**53 - 1, strict=True)
    offset: int = Field(ge=0, le=2**53 - 1, strict=True)


class RelayTerminal(DomainModel):
    version: Literal[1] = 1
    kind: Literal["terminal"] = "terminal"
    request_id: ObjectId
    use: UseIdentity
    result: JsonValue = Field(default=None, repr=False)
    error: RelayFailure | None = None
    transfer: TransferPosition | None = None

    @model_validator(mode="after")
    def _outcome(self) -> RelayTerminal:
        if self.error is not None and self.result is not None:
            raise ValueError("Relay terminal outcomes cannot contain both a result and an error")
        return self


class RelayChunk(DomainModel):
    version: Literal[1] = 1
    kind: Literal["chunk"] = "chunk"
    request_id: ObjectId
    use: UseIdentity
    transfer: TransferPosition
    data: str = Field(repr=False)


class RelayCredit(DomainModel):
    version: Literal[1] = 1
    kind: Literal["credit"] = "credit"
    request_id: ObjectId
    use: UseIdentity
    transfer: TransferPosition


class RelayFinish(DomainModel):
    version: Literal[1] = 1
    kind: Literal["finish"] = "finish"
    request_id: ObjectId
    use: UseIdentity
    transfer: TransferPosition


type RelayInput = Annotated[RelayChunk | RelayCredit | RelayFinish, Field(discriminator="kind")]
RELAY_INPUT = TypeAdapter[RelayInput](RelayInput)
type RelayFrame = Annotated[RelayTerminal | RelayChunk | RelayCredit, Field(discriminator="kind")]
RELAY_FRAME = TypeAdapter[RelayFrame](RelayFrame)


def canonical_message(
    message: RelayRequest | RelayTerminal | RelayChunk | RelayCredit | RelayFinish, *, max_bytes: int
) -> str:
    """Retain exact canonical input, including bigint Attempt fences, for deduplication."""
    encoded = json.dumps(message.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode()) > max_bytes:
        raise ValueError("Relay message exceeds its byte budget")
    return encoded
