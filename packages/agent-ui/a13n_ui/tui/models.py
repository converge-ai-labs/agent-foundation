"""Immutable bounded semantic state for the terminal workstation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from a13n_ui.surfaces import (
    DecisionBatchView,
    LaunchProjectResolution,
    NewThreadDefaults,
    ProjectPathCompletionPage,
    ProjectSummary,
    ReviewView,
    RootOperationView,
    SkillCatalogView,
    SkillReference,
    TaskPage,
    ThreadDetail,
    ThreadFocusSnapshot,
    ThreadSelectorCatalog,
    WorkbenchPage,
    WorkbenchThreadView,
)

MAX_DRAFTS = 16
MAX_NOTICES = 32
MAX_TIMELINE_BLOCKS = 500
MAX_BLOCK_TEXT = 256 * 1024

type ConfigurationResourceKind = Literal[
    "agent",
    "environment",
    "harness_plugin",
    "environment_run_extension",
    "mcp_server",
]


class TerminalLifecycle(StrEnum):
    STARTING = "starting"
    READY = "ready"
    CLOSING = "closing"
    FAILED = "failed"


class TerminalMode(StrEnum):
    FOCUS = "focus"
    WORKBENCH = "workbench"


class ControlMode(StrEnum):
    DRAFT = "draft"
    IDLE = "idle"
    PREPARING = "preparing"
    RUNNING = "running"
    AWAITING_DECISION = "awaiting_decision"
    CANCELLING = "cancelling"
    UNAVAILABLE = "unavailable"


class BlockKind(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    REASONING = "reasoning"
    TOOL = "tool"
    CHILD = "child"
    TASK = "task"
    NOTICE = "notice"
    FAILURE = "failure"


class BlockStatus(StrEnum):
    PROVISIONAL = "provisional"
    RUNNING = "running"
    CLOSED = "closed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class TimelineBlock:
    block_id: str
    thread_id: str
    kind: BlockKind
    status: BlockStatus
    version: int = 1
    run_id: str | None = None
    execution_id: str | None = None
    receipt_id: str | None = None
    tool_call_id: str | None = None
    task_id: str | None = None
    source_text: str | None = None
    summary: str | None = None
    detail_available: bool = False
    retained_position: int | None = None
    available_actions: tuple[Literal["wait", "steer", "cancel"], ...] = ()
    provisional: bool = False


@dataclass(frozen=True, slots=True)
class ReadingAnchor:
    block_id: str
    line_offset: int = 0


@dataclass(frozen=True, slots=True)
class DraftState:
    key: str
    text: str = ""
    cursor: int = 0
    project_paths: tuple[str, ...] = ()
    skill_references: tuple[SkillReference, ...] = ()
    editor_revision: int = 0
    touched: int = 0


@dataclass(frozen=True, slots=True)
class DecisionAnswerDraft:
    request_id: str
    question_answers: tuple[tuple[str, tuple[str, ...]], ...] = ()
    response_text: str = ""
    action: Literal["approve", "override", "deny", "result"] | None = None
    payload_text: str = ""
    denial_reason: str = ""


@dataclass(frozen=True, slots=True)
class DecisionSessionState:
    thread_id: str
    continuation_id: str
    request_ids: tuple[str, ...]
    request_index: int = 0
    question_index: int = 0
    answers: tuple[DecisionAnswerDraft, ...] = ()
    validation_message: str | None = None

    def answer(self, request_id: str) -> DecisionAnswerDraft:
        return next(
            (item for item in self.answers if item.request_id == request_id),
            DecisionAnswerDraft(request_id=request_id),
        )


@dataclass(frozen=True, slots=True)
class ReviewState:
    key: str
    view: ReviewView
    request_version: int
    thread_id: str | None = None
    execution_id: str | None = None
    available_actions: tuple[Literal["wait", "steer", "cancel"], ...] = ()


@dataclass(frozen=True, slots=True)
class OverlayState:
    kind: Literal[
        "commands",
        "threads",
        "skills",
        "status",
        "help",
        "configuration",
        "projects",
        "review",
        "inspector",
        "exit",
    ]
    key: str | None = None
    context_key: str | None = None
    active_root_operations: int = 0
    active_child_executions: int = 0


@dataclass(frozen=True, slots=True)
class CompletionState:
    request_version: int
    key: str
    kind: Literal["path", "skill"]
    query: str
    token_start: int
    token_end: int
    paths: ProjectPathCompletionPage | None = None
    skills: SkillCatalogView | None = None


@dataclass(frozen=True, slots=True)
class ConfigurationConflictState:
    thread_id: str
    kind: ConfigurationResourceKind
    resource_id: str
    intended_selected: bool
    message: str


@dataclass(frozen=True, slots=True)
class TerminalNotice:
    notice_id: str
    severity: Literal["info", "warning", "error"]
    message: str
    code: str | None = None
    thread_id: str | None = None


@dataclass(frozen=True, slots=True)
class ThreadViewState:
    thread_id: str
    detail: ThreadDetail | None = None
    snapshot: ThreadFocusSnapshot | None = None
    root_operation: RootOperationView | None = None
    tasks: TaskPage = field(default_factory=TaskPage)
    decisions: DecisionBatchView | None = None
    decision_session: DecisionSessionState | None = None
    stale_decision_session: DecisionSessionState | None = None
    timeline: tuple[TimelineBlock, ...] = ()
    control_mode: ControlMode = ControlMode.UNAVAILABLE
    epoch: str | None = None
    last_sequence: int = 0
    projection_version: int = 0
    transcript_continuation_id: str | None = None
    older_cursor: str | None = None
    follow_latest: bool = True
    pending_output: int = 0
    selected_block_id: str | None = None
    reading_anchor: ReadingAnchor | None = None
    cancelling_receipt_id: str | None = None
    unretained_output: bool = False


@dataclass(frozen=True, slots=True)
class WorkbenchState:
    page: WorkbenchPage | None = None
    query: str = ""
    selected_thread_id: str | None = None
    acknowledged_receipts: frozenset[str] = frozenset()
    projection_version: int = 0

    @property
    def rows(self) -> tuple[WorkbenchThreadView, ...]:
        return () if self.page is None else self.page.rows


@dataclass(frozen=True, slots=True)
class TerminalState:
    lifecycle: TerminalLifecycle = TerminalLifecycle.STARTING
    mode: TerminalMode = TerminalMode.FOCUS
    launch_resolution: LaunchProjectResolution | None = None
    launch_project_id: str | None = None
    project_filter_id: str | None = None
    focused_thread_id: str | None = None
    previous_focused_thread_id: str | None = None
    draft_defaults: NewThreadDefaults = field(default_factory=NewThreadDefaults)
    workbench: WorkbenchState = field(default_factory=WorkbenchState)
    drafts: tuple[DraftState, ...] = (DraftState(key="new"),)
    thread_views: tuple[ThreadViewState, ...] = ()
    overlays: tuple[OverlayState, ...] = ()
    review: ReviewState | None = None
    projects: tuple[ProjectSummary, ...] = ()
    selectors: ThreadSelectorCatalog | None = None
    skill_catalog: SkillCatalogView | None = None
    thread_picker: WorkbenchPage | None = None
    thread_picker_query: str = ""
    overlay_request_version: int = 0
    completion: CompletionState | None = None
    configuration_conflict: ConfigurationConflictState | None = None
    notices: tuple[TerminalNotice, ...] = ()
    show_reasoning: bool = False
    show_tool_details: bool = False
    logical_clock: int = 0

    def thread_view(self, thread_id: str) -> ThreadViewState | None:
        return next((item for item in self.thread_views if item.thread_id == thread_id), None)

    def draft(self, key: str) -> DraftState | None:
        return next((item for item in self.drafts if item.key == key), None)


@dataclass(frozen=True, slots=True)
class ProjectionHints:
    changed: frozenset[Literal["lifecycle", "route", "workbench", "focus", "composer", "overlay", "notice"]]
    scroll_to_latest: bool = False
    preserve_anchor: ReadingAnchor | None = None


@dataclass(frozen=True, slots=True)
class Reduction:
    state: TerminalState
    hints: ProjectionHints


__all__ = [
    "MAX_BLOCK_TEXT",
    "MAX_DRAFTS",
    "MAX_NOTICES",
    "MAX_TIMELINE_BLOCKS",
    "BlockKind",
    "BlockStatus",
    "CompletionState",
    "ConfigurationConflictState",
    "ConfigurationResourceKind",
    "ControlMode",
    "DecisionAnswerDraft",
    "DecisionSessionState",
    "DraftState",
    "OverlayState",
    "ProjectionHints",
    "ReadingAnchor",
    "Reduction",
    "ReviewState",
    "TerminalLifecycle",
    "TerminalMode",
    "TerminalNotice",
    "TerminalState",
    "ThreadViewState",
    "TimelineBlock",
    "WorkbenchState",
]
