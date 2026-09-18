"""Public accepted mount identities and safe application observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Literal

from a13n_harness import SafeFailure
from pydantic import StringConstraints, field_validator

from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import ObjectId
from a13n_service.temporal import assume_utc

from .domain import DomainModel

if TYPE_CHECKING:
    from .mount_models import RunEnvironmentMountRecord


MountName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,62}$", max_length=63)]
type MountApplicationStatus = Literal["pending", "preparing", "ready", "failed"]


class AddEnvironmentMountRequest(DomainModel):
    name: MountName
    environment_id: ObjectId

    @field_validator("name")
    @classmethod
    def named_addition(cls, value: str) -> str:
        if value == "workspace":
            raise ValueError("workspace is reserved for the primary Environment")
        return value


class RunEnvironmentMount(DomainModel):
    run_id: ObjectId
    name: MountName
    environment_id: ObjectId
    created_at: datetime
    accepting_principal: PrincipalRef
    use_started_at: datetime | None = None
    applied_attempt_id: ObjectId | None = None
    applied_attempt_fence: int | None = None
    application_status: MountApplicationStatus = "pending"
    observed_at: datetime | None = None
    error: SafeFailure | None = None


@dataclass(frozen=True, slots=True)
class AcceptedRunMount:
    """Immutable acceptance facts; loading state is deliberately not installation evidence."""

    run_id: str
    name: str
    environment_id: str
    created_at: datetime

    @classmethod
    def from_record(cls, row: RunEnvironmentMountRecord) -> AcceptedRunMount:
        return cls(row.run_id, row.name, row.environment_id, assume_utc(row.created_at))
