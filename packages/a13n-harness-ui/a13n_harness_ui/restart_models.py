"""Detached graceful restart handoff values; never a general execution queue."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from a13n_harness_ui.storage.objects import ObjectRef


class RestartPrincipal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issuer: str
    subject: str
    claims: dict[str, str]


class RestartItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    thread_id: str
    run_id: str
    composition: ObjectRef
    checkpoint: ObjectRef
    execution_id: str | None = None
    parent_thread_id: str | None = None
    root_thread_id: str
    parent_composition: ObjectRef | None = None
    parent_run_id: str | None = None
    identity: RestartPrincipal | None = None
    parent_identity: RestartPrincipal | None = None
    usage_limits: dict[str, int | float | bool | None] | None = None


class RestartResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    thread_id: str
    execution_id: str | None = None
    state: Literal["restoring", "restored", "blocked"]
    message: str | None = None
    resumed_run_id: str | None = None
    resumed_execution_id: str | None = None


class RestartBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str
    state: Literal["ready", "consumed"]
    items: tuple[RestartItem, ...] = ()
    results: tuple[RestartResult, ...] = ()
    error: str | None = None
