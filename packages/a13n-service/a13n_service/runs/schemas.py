"""What callers submit to threads and read back about sessions, threads, input and runs."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import PageLimit
from a13n_service.infra.ids import ObjectId
from a13n_service.infra.labels import Labels
from a13n_service.resources.agents.schemas import AgentOverride
from a13n_service.resources.connections.headers import normalize_headers
from a13n_service.resources.memories.schemas import MemoryMount, MemoryMounts
from a13n_service.runs.display import Item
from a13n_service.runs.environments.schemas import MAX_MOUNTS, MountCreate

type RunStatus = Literal["accepted", "running", "waiting", "completed", "failed", "cancelled"]
type Trigger = Literal["input", "queued", "resume", "child_result", "spawned"]
type Lineage = Literal["root", "continue", "fork"]
type EntryStatus = Literal["pending", "assigned", "consumed", "failed", "withdrawn"]
type Delivery = Literal["steer", "next_run"]
type WaitReason = Literal["approval", "call", "multiple"]
type Sealed = Literal["waiting", "completed", "failed", "cancelled"]

MAX_FAILURE_MESSAGE_CHARS = 4096
MAX_JSON_DEPTH = 32
MAX_RESUME_BYTES = 262144


def canonical_json(value: object) -> bytes:
    """The one serialization used for sizes and digests of stored payloads and requests."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Failure(_Frozen):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(max_length=MAX_FAILURE_MESSAGE_CHARS)

    @classmethod
    def of(cls, error: ServiceError) -> "Failure":
        """A refusal as a failure; a conflict's reason names it more precisely than `conflict`."""
        reason = error.details.get("reason") if error.code == "conflict" else None
        return cls(
            code=reason if isinstance(reason, str) else error.code, message=error.message[:MAX_FAILURE_MESSAGE_CHARS]
        )


class TextPart(_Frozen):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=65536)


class AssetPart(_Frozen):
    type: Literal["asset"]
    asset_id: ObjectId


class UrlPart(_Frozen):
    type: Literal["url"]
    url: str = Field(min_length=8, max_length=2048)

    @field_validator("url")
    @classmethod
    def plain_http(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("URL input requires a plain HTTP(S) URL")
        return value


class JsonPart(_Frozen):
    type: Literal["json"]
    value: JsonValue

    @field_validator("value")
    @classmethod
    def bounded(cls, value: JsonValue) -> JsonValue:
        def check(item: JsonValue, depth: int) -> None:
            if depth > MAX_JSON_DEPTH:
                raise ValueError("Structured input nesting exceeds its limit")
            children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
            for child in children:
                check(child, depth + 1)

        check(value, 0)
        return value


type Part = Annotated[TextPart | AssetPart | UrlPart | JsonPart, Field(discriminator="type")]


class MessagePayload(_Frozen):
    content: tuple[Part, ...] = Field(min_length=1, max_length=32)

    def text(self) -> str:
        return "\n".join(part.text for part in self.content if isinstance(part, TextPart))


class UsageLimit(_Frozen):
    requests: int = Field(ge=1, le=10000)


class RunOptions(_Frozen):
    """What a message may choose for the run it starts. A steer joins a run with the defaults or equal options."""

    labels: Labels = Field(default_factory=dict)
    max_usage: UsageLimit | None = None
    # Changes to the revision's configuration for this run only. Submission validates them; acceptance freezes
    # them into the run's options with their pins resolved, validating them again in any later transaction.
    overrides: AgentOverride | None = None

    def digest(self) -> str:
        """What a steer's options must match: the options as submitted, since acceptance freezes the run's."""
        return hashlib.sha256(canonical_json(self.model_dump(mode="json"))).hexdigest()


def _normalize_mcp_headers(value: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    normalized = {connection: normalize_headers(headers) for connection, headers in value.items()}
    if sum(len(name) + len(item) for headers in normalized.values() for name, item in headers.items()) > 16384:
        raise ValueError("Caller headers exceed their byte limit")
    return normalized


# A thread's caller headers by connection, normalized and bounded wherever they are accepted.
type McpHeaders = Annotated[
    dict[ObjectId, dict[str, str]], Field(max_length=32), AfterValidator(_normalize_mcp_headers)
]


# A new thread's desired mounts, added with the checks of `POST .../threads/{thread}/environments` before its
# first run is accepted; a `workspace` mount replaces the primary sandbox the agent's template would reserve.
type InitialMounts = Annotated[tuple[MountCreate, ...], Field(max_length=MAX_MOUNTS)]


class Message(_Frozen):
    kind: Literal["message"] = "message"
    delivery: Delivery = "steer"
    payload: MessagePayload
    agent_id: ObjectId
    agent_revision_id: ObjectId | None = None
    options: RunOptions = Field(default_factory=RunOptions)


class NewThread(Message):
    session_id: ObjectId | None = None
    mcp_headers: McpHeaders = Field(default_factory=dict)
    environments: InitialMounts = ()
    # Mounted with the checks of `POST .../threads/{thread}/memories` before the first run is accepted.
    memories: MemoryMounts = ()


class Fork(Message):
    # Leave out the origin thread's desired mounts, which a fork otherwise shares.
    fresh_environments: bool = False
    # Mounted in addition to the shared ones.
    environments: InitialMounts = ()
    # Mounted in addition to the origin thread's memory mounts, which a fork copies.
    memories: MemoryMounts = ()


class ThreadUpdate(_Frozen):
    labels: Labels | None = None
    mcp_headers: McpHeaders | None = None


class EntryUpdate(_Frozen):
    """Pending entries only; the original request digest never changes."""

    delivery: Delivery | None = None
    payload: MessagePayload | None = None
    agent_revision_id: ObjectId | None = None
    options: RunOptions | None = None


class InboxOrder(_Frozen):
    entry_ids: tuple[ObjectId, ...] = Field(min_length=1, max_length=512)

    @field_validator("entry_ids")
    @classmethod
    def unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Entry IDs must be unique")
        return value


class Approve(_Frozen):
    action: Literal["approve"]


class Deny(_Frozen):
    action: Literal["deny"]
    reason: str | None = Field(default=None, max_length=4096)


class Returned(_Frozen):
    """A JSON tool result. Built-in question values are validated by the Harness."""

    status: Literal["returned"]
    value: JsonValue


class Failed(_Frozen):
    """An explicit external tool failure, including an intentional unanswered question."""

    status: Literal["failed"]
    message: str = Field(min_length=1, max_length=4096, pattern=r"\S")


type ApprovalDecision = Annotated[Approve | Deny, Field(discriminator="action")]
type CallResult = Annotated[Returned | Failed, Field(discriminator="status")]
type ToolCallId = Annotated[str, Field(min_length=1, max_length=1024, pattern=r"\S")]


class Resume(_Frozen):
    """The complete result batch, submitted and stored on the successor without omission defaults."""

    approvals: dict[ToolCallId, ApprovalDecision]
    calls: dict[ToolCallId, CallResult]

    @model_validator(mode="after")
    def bounded(self) -> "Resume":
        if self.approvals.keys() & self.calls.keys():
            raise ValueError("A call ID cannot occur in both result categories")
        if len(self.approvals) + len(self.calls) > 128:
            raise ValueError("Resume supports at most 128 results")
        if len(canonical_json(self.model_dump(mode="json"))) > MAX_RESUME_BYTES:
            raise ValueError("Resume request exceeds its byte limit")
        return self


class PendingCall(_Frozen):
    tool_call_id: ToolCallId
    tool_name: str
    arguments: dict[str, JsonValue]
    presentation: dict[str, JsonValue] | None = None


class Pending(_Frozen):
    """Public projection; complete native requests and private metadata stay in the checkpoint."""

    approvals: tuple[PendingCall, ...]
    calls: tuple[PendingCall, ...]

    @model_validator(mode="after")
    def bounded(self) -> "Pending":
        items = (*self.approvals, *self.calls)
        if not 1 <= len(items) <= 128:
            raise ValueError("Pending requires one to 128 calls")
        if len({item.tool_call_id for item in items}) != len(items):
            raise ValueError("Pending call IDs must be unique across categories")
        return self

    @property
    def reason(self) -> WaitReason:
        if self.approvals and self.calls:
            return "multiple"
        return "approval" if self.approvals else "call"


class Outcome(_Frozen):
    """How a run ends. A worker commits a completed or waiting outcome in its final checkpoint before sealing."""

    status: Sealed
    output: JsonValue = None
    pending: Pending | None = None
    failure: Failure | None = None

    @model_validator(mode="after")
    def consistent(self) -> "Outcome":
        if (self.status == "waiting") != (self.pending is not None):
            raise ValueError("Only a waiting outcome carries pending requests, and it requires them")
        if (self.status in {"failed", "cancelled"}) != (self.failure is not None):
            raise ValueError("Failed and cancelled outcomes require failure details; other outcomes cannot carry them")
        if self.status != "completed" and self.output is not None:
            raise ValueError("Only a completed outcome carries output")
        return self

    @classmethod
    def failed(cls, code: str, message: str) -> "Outcome":
        return cls(status="failed", failure=Failure(code=code, message=message[:MAX_FAILURE_MESSAGE_CHARS]))

    @classmethod
    def refused(cls, error: ServiceError) -> "Outcome":
        return cls(status="failed", failure=Failure.of(error))

    @classmethod
    def cancelled(cls) -> "Outcome":
        return cls(status="cancelled", failure=Failure(code="cancelled", message="The run was interrupted"))


class EnvironmentMount(_Frozen):
    name: str
    environment_id: str
    working_directory: str | None = None


class SessionCreate(_Frozen):
    labels: Labels = Field(default_factory=dict)


class SessionUpdate(_Frozen):
    labels: Labels


class SessionQuery(_Frozen):
    """One page of the session list and its filters. Agent, status and trigger match the session's latest run,
    the one its preview shows."""

    q: str | None = Field(default=None, max_length=72, description="A session or thread ID")
    agent_id: str | None = Field(default=None, max_length=72)
    status: tuple[RunStatus, ...] = Field(default=(), max_length=6)
    trigger: tuple[Trigger, ...] = Field(default=(), max_length=5)
    updated_after: AwareDatetime | None = None
    updated_before: AwareDatetime | None = None
    label: tuple[str, ...] = Field(default=(), max_length=8)
    limit: PageLimit = 50
    cursor: str | None = None


class SessionPreview(BaseModel):
    """The session's latest run, summarized for a list row."""

    run_id: str
    thread_id: str
    agent_id: str
    agent_name: str
    status: RunStatus
    trigger: Trigger
    input_text: str | None
    output_text: str | None


class SessionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    labels: dict[str, str]
    created_by_id: str
    last_run_id: str | None
    run_count: int = 0
    preview: SessionPreview | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class SessionPage(BaseModel):
    items: list[SessionView]
    next_cursor: str | None


class ThreadView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    session_id: str
    origin: Literal["new", "fork", "child"]
    origin_thread_id: str | None
    origin_run_id: str | None
    origin_tool_call_id: str | None
    # The subagent edge that spawned a child thread; NULL unless `origin` is `child`.
    subagent: str | None
    current_run_id: str | None
    head_run_id: str | None
    last_run_id: str | None
    mcp_headers: dict[str, dict[str, str]]
    labels: dict[str, str]
    archived_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class ThreadPage(BaseModel):
    items: list[ThreadView]
    next_cursor: str | None


class EntryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    thread_id: str
    kind: Literal["message", "child_result"]
    delivery: Delivery
    position: int
    status: EntryStatus
    principal_id: str
    payload: dict[str, JsonValue]
    agent_id: str | None
    agent_revision_id: str | None
    options: RunOptions
    child_run_id: str | None
    origin_run_id: str | None
    assigned_run_id: str | None
    incorporated_checkpoint_seq: int | None
    failure: Failure | None
    created_at: datetime
    finished_at: datetime | None


class EntryPage(BaseModel):
    items: list[EntryView]
    next_cursor: str | None


class RunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    session_id: str
    thread_id: str
    agent_id: str
    agent_revision_id: str
    revision_selection: Literal["pinned", "default", "inherited"]
    principal_id: str
    status: RunStatus
    trigger: Trigger
    lineage: Lineage
    parent_run_id: str | None
    source_entry_id: str | None
    # The source entry's payload: the request a Console shows for this run. Resumed runs carry `resume` instead.
    input: dict[str, JsonValue] | None = None
    resume: Resume | None
    resumed_by_id: str | None
    wait_reason: WaitReason | None
    pending: Pending | None
    environment_mounts: list[EnvironmentMount]
    memory_mounts: list[MemoryMount]
    # The options its message chose, frozen at acceptance; a resume or child result inherits its origin's.
    options: RunOptions
    current_attempt_id: str | None
    # Attempts charged to `max_attempts`: the initial one and each recovery. A handoff is not charged, so the
    # attempt list can hold more.
    attempts: int
    max_attempts: int
    output: JsonValue | None
    failure: Failure | None
    usage_at_seal: dict[str, JsonValue] | None
    labels: dict[str, str]
    cancel_requested_at: datetime | None
    version: int
    created_at: datetime
    started_at: datetime | None
    sealed_at: datetime | None
    updated_at: datetime


class RunPage(BaseModel):
    items: list[RunView]
    next_cursor: str | None


class RunLabels(_Frozen):
    labels: Labels


class RunItems(BaseModel):
    """A run's committed display with the run it describes. Live output continues after `position`."""

    run: RunView
    items: list[Item]
    # The "{attempt}-{sequence}" stream position the items cover; None until the first checkpoint.
    position: str | None
    # Earlier items the display dropped over its item limit.
    dropped: int
    # Execution sealed: the items are final and live output no longer applies.
    complete: bool


class Attempts(BaseModel):
    items: list["AttemptView"]


class AttemptView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    run_id: str
    number: int
    status: Literal["leased", "running", "succeeded", "yielded", "failed", "cancelled"]
    start_reason: Literal["initial", "recovery", "handoff"]
    replaces_attempt_id: str | None
    worker_build: str
    harness_run_id: str | None
    yield_reason: str | None
    failure: Failure | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class Submitted(BaseModel):
    """A submission receipt: the entry and, when the thread was idle, the run it started."""

    thread: ThreadView
    entry: EntryView
    run: RunView | None


class UsageFilter(_Frozen):
    run_id: ObjectId | None = None
    thread_id: ObjectId | None = None
    session_id: ObjectId | None = None
    ingested_after: AwareDatetime | None = None
    ingested_before: AwareDatetime | None = None


class ModelUsage(BaseModel):
    # The model's key; None for records no model priced.
    model: str | None
    requests: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    # The sum of the costs priced at dispatch; None when no record of the model was priced.
    cost: Decimal | None


class UsageSummary(BaseModel):
    models: list[ModelUsage]
