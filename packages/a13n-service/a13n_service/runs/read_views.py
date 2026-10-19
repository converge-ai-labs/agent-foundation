"""Deterministic Run and inbox summaries; full values remain at their authorized detail routes."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from a13n_service.infra.reads import Preview, bounded, preview
from a13n_service.runs.schemas import EntryStatus, EntryView, RunContent, RunStatus, RunView, Trigger, WaitReason


class FailureSummary(BaseModel):
    code: str
    message: Preview


class RunSummary(BaseModel):
    view: Literal["compact"] = "compact"
    id: str
    workspace_id: str
    session_id: str
    thread_id: str
    agent_id: str
    agent_name: str | None
    agent_revision_id: str
    revision_selection: Literal["pinned", "default", "inherited"]
    principal_id: str
    parent_run_id: str | None
    source_entry_id: str | None
    current_attempt_id: str | None
    lineage: Literal["root", "continue", "fork"]
    status: RunStatus
    trigger: Trigger
    attempts: int
    max_attempts: int
    wait_reason: WaitReason | None
    failure: FailureSummary | None
    input: Preview
    output: Preview
    usage: Preview
    version: int
    created_at: datetime
    started_at: datetime | None
    sealed_at: datetime | None
    updated_at: datetime
    omitted_sections: tuple[str, ...] = (
        "input",
        "output",
        "resume",
        "pending",
        "options",
        "environment_mounts",
        "memory_mounts",
        "usage_at_seal",
        "labels",
    )


class RunSummaryPage(BaseModel):
    items: list[RunSummary]
    next_cursor: str | None


def run_summary(value: RunView, agent_name: str | None = None) -> RunSummary:
    fields = RunSummary.model_fields.keys() - {
        "view",
        "agent_name",
        "failure",
        "input",
        "output",
        "usage",
        "omitted_sections",
    }
    return bounded(
        RunSummary(
            agent_name=agent_name,
            **{name: getattr(value, name) for name in fields},
            failure=FailureSummary(code=value.failure.code, message=preview(value.failure.message, 500))
            if value.failure
            else None,
            input=preview(value.input),
            output=preview(value.output),
            usage=preview(value.usage_at_seal),
        )
    )


class EntrySummary(BaseModel):
    view: Literal["compact"] = "compact"
    id: str
    thread_id: str
    kind: Literal["message", "child_result"]
    delivery: Literal["steer", "next_run"]
    status: EntryStatus
    position: int
    principal_id: str
    agent_id: str | None
    agent_revision_id: str | None
    child_run_id: str | None
    origin_run_id: str | None
    assigned_run_id: str | None
    created_at: datetime
    finished_at: datetime | None
    payload: Preview
    failure: FailureSummary | None
    omitted_sections: tuple[str, ...] = ("payload", "options")


class EntrySummaryPage(BaseModel):
    items: list[EntrySummary]
    next_cursor: str | None


def entry_summary(value: EntryView) -> EntrySummary:
    fields = EntrySummary.model_fields.keys() - {"view", "payload", "failure", "omitted_sections"}
    return bounded(
        EntrySummary(
            **{name: getattr(value, name) for name in fields},
            payload=preview(value.payload),
            failure=FailureSummary(code=value.failure.code, message=preview(value.failure.message, 500))
            if value.failure
            else None,
        )
    )


class ContentSummary(BaseModel):
    view: Literal["compact"] = "compact"
    id: str
    media_type: str
    size_chars: int
    value: Preview
    source_truncated: bool
    omitted_sections: tuple[str, ...] = ("value",)


def content_summary(value: RunContent) -> ContentSummary:
    from a13n_service.infra.reads import json_text

    text = value.value if isinstance(value.value, str) else json_text(value.value)
    return bounded(
        ContentSummary(
            id=value.id,
            media_type=value.media_type,
            size_chars=len(text),
            value=preview(text, 2000),
            source_truncated=value.truncated,
        ),
        budget=8500,
    )
