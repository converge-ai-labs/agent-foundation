"""Typed Kubernetes observations exposed by registry-created management providers."""

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.kubernetes.configuration import Resource
from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ResourceObservation(BaseModel):
    reference: Resource
    observation: Literal["present", "absent", "unknown", "conflict"]
    reason: str | None = None
    created_at: str | None = None
    deleting_at: str | None = None
    finalizers: list[str] = Field(default_factory=list)
    status: dict[str, JsonValue] = Field(default_factory=dict)


class Event(BaseModel):
    resource_uid: str
    type: str | None = None
    reason: str | None = None
    message: str | None = None
    count: int | None = None
    first_timestamp: str | None = None
    last_timestamp: str | None = None


class Diagnostics(BaseModel):
    observed_at: datetime
    cluster: str
    namespace: str
    ownership_id: str
    pod_name: str
    pod_uid: str | None
    stage: str
    connection: str
    # This is the last published lifecycle observation, never a new readiness grant.
    envd: Literal["previously_ready", "not_verified"]
    resources: list[ResourceObservation]
    rendered: list[dict[str, JsonValue]]
    events: list[Event]
    events_truncated: bool
    errors: list[str]
    commands: list[str]


class LogQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    container: str | None = None
    previous: bool = False
    tail_lines: int = Field(default=100, ge=1, le=1000)


class Logs(BaseModel):
    pod_uid: str
    container: str
    previous: bool
    text: str
    limit_bytes: int = 65536


@runtime_checkable
class KubernetesObserver(Protocol):
    async def kubernetes_diagnostics(
        self, environment: object, *, environment_id: str, state: EnvironmentState
    ) -> Diagnostics: ...

    async def kubernetes_logs(
        self, environment: object, *, environment_id: str, state: EnvironmentState, query: LogQuery
    ) -> Logs: ...
