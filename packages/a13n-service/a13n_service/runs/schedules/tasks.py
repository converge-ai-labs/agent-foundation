"""Task-oriented Schedule authoring, preserving the existing lifecycle authority."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from a13n_service.execution_policy import ExecutionOverride
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import ObjectId
from a13n_service.infra.labels import Labels
from a13n_service.resources.agents.schemas import AgentOverride
from a13n_service.resources.memories.schemas import MemoryMounts
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schedules import queries, service
from a13n_service.runs.schedules.schemas import (
    Schedule,
    ScheduleInvocation,
    ScheduleRule,
    ScheduleTarget,
    ScheduleUpdate,
)
from a13n_service.runs.schemas import JsonPart, MessagePayload, RunConfiguration, RunOptions, TextPart, UsageLimit
from a13n_service.tenancy.authorize import Principal


class TaskOptions(BaseModel):
    """Execution choices; omitted fields retain the saved choice on an edit.

    Caller authentication/context headers are owned by trusted submission flows.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    labels: Labels = Field(default_factory=dict)
    configuration: RunConfiguration | None = None
    execution: ExecutionOverride | None = None
    max_usage: UsageLimit | None = None
    overrides: AgentOverride | None = None


class TaskChanges(BaseModel):
    """Omission preserves; arrays replace; null clears description or a revision pin only."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2048)
    labels: Labels | None = None
    schedule: ScheduleRule | None = None
    target: ScheduleTarget | None = None
    max_lateness_seconds: int | None = Field(default=None, ge=60, le=86400)
    instructions: str | None = Field(default=None, min_length=1, max_length=65536)
    structured_inputs: tuple[JsonValue, ...] | None = Field(default=None, max_length=31)
    agent_id: ObjectId | None = None
    agent_revision_id: ObjectId | None = None
    options: TaskOptions | None = None
    memories: MemoryMounts | None = None

    @model_validator(mode="after")
    def clearable(self) -> Self:
        for field in self.model_fields_set - {"description", "agent_revision_id"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


def invocation(
    agent_id: str,
    agent_revision_id: str | None,
    instructions: str,
    structured_inputs: tuple[JsonValue, ...],
    options: TaskOptions,
    memories: MemoryMounts,
) -> ScheduleInvocation:
    return ScheduleInvocation(
        agent_id=agent_id,
        agent_revision_id=agent_revision_id,
        payload=MessagePayload(
            content=(
                TextPart(type="text", text=instructions),
                *(JsonPart(type="json", value=value) for value in structured_inputs),
            )
        ),
        options=RunOptions.model_validate(options.model_dump()),
        memories=memories,
    )


def apply_changes(current: Schedule, changes: TaskChanges) -> ScheduleUpdate:
    supplied = changes.model_fields_set
    values = {name: getattr(changes, name) for name in supplied & ScheduleUpdate.model_fields.keys()}
    task_fields = {"instructions", "structured_inputs", "agent_id", "agent_revision_id", "options", "memories"}
    if supplied & task_fields:
        saved = current.invocation
        parts = list(saved.payload.content)
        if "instructions" in supplied:
            assert changes.instructions is not None
            parts = [
                TextPart(type="text", text=changes.instructions),
                *(part for part in parts if not isinstance(part, TextPart)),
            ]
        if "structured_inputs" in supplied:
            assert changes.structured_inputs is not None
            parts = [
                *(part for part in parts if not isinstance(part, JsonPart)),
                *(JsonPart(type="json", value=value) for value in changes.structured_inputs),
            ]
        updated = dict(saved)
        updated["payload"] = MessagePayload(content=tuple(parts))
        for name in ("agent_id", "agent_revision_id", "memories"):
            if name in supplied:
                updated[name] = getattr(changes, name)
        # A different executor without an explicit pin selects that executor's default.
        if "agent_id" in supplied and changes.agent_id != saved.agent_id and "agent_revision_id" not in supplied:
            updated["agent_revision_id"] = None
        if changes.options is not None:
            updated["options"] = RunOptions.model_validate(
                {
                    **dict(saved.options),
                    **{name: getattr(changes.options, name) for name in changes.options.model_fields_set},
                }
            )
        values["invocation"] = ScheduleInvocation.model_validate(updated)
    return ScheduleUpdate.model_validate(values)


async def update_task(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    schedule_id: str,
    changes: TaskChanges,
    *,
    expected_version: str,
) -> Schedule:
    current = await queries.get(runtime.storage, actor, workspace_id, schedule_id)
    require_match(expected_version, current.id, current.version)
    body = apply_changes(current, changes)
    # The same observed precondition is arbitrated in the authoritative mutation.
    return await service.update(runtime, actor, workspace_id, schedule_id, body, if_match=expected_version)
