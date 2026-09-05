"""Normalized App facts consumed by the pure terminal reducer."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from a13n_ui.live import LiveEvent
from a13n_ui.surfaces import (
    ChildControlResult,
    DecisionBatchView,
    FailureView,
    LaunchProjectResolution,
    NewThreadDefaults,
    ProjectSummary,
    ReviewView,
    RootControlResult,
    RootOperationView,
    RootRunReceipt,
    SkillCatalogView,
    SkillReference,
    TaskView,
    ThreadActivityPage,
    ThreadFocusSnapshot,
    ThreadSelectorCatalog,
    TranscriptPage,
)
from a13n_ui.tui.models import (
    CompletionState,
    ConfigurationConflictState,
    DecisionAnswerDraft,
    OverlayState,
    ReadingAnchor,
)


@dataclass(frozen=True, slots=True)
class StreamPartEvent:
    epoch: str
    sequence: int
    thread_id: str
    run_id: str
    execution_id: str | None
    part_id: str
    kind: Literal["assistant", "reasoning"]
    action: Literal["open", "append", "close"]
    text: str = ""
    root_thread_id: str | None = None
    parent_thread_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolEvent:
    epoch: str
    sequence: int
    thread_id: str
    run_id: str
    execution_id: str | None
    tool_call_id: str
    action: Literal["open", "arguments", "close", "result"]
    tool_name: str | None = None
    text: str = ""
    root_thread_id: str | None = None
    parent_thread_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunHintEvent:
    epoch: str
    sequence: int
    thread_id: str
    run_id: str
    execution_id: str | None
    action: Literal["started", "finished", "error"]
    message: str | None = None
    root_thread_id: str | None = None
    parent_thread_id: str | None = None


@dataclass(frozen=True, slots=True)
class UnknownLiveEvent:
    epoch: str
    sequence: int
    thread_id: str
    run_id: str
    execution_id: str | None
    event_type: str
    summary: str
    root_thread_id: str | None = None
    parent_thread_id: str | None = None


@dataclass(frozen=True, slots=True)
class TaskChangedEvent:
    epoch: str
    sequence: int
    thread_id: str
    run_id: str
    execution_id: str | None
    task_state_version: int
    task: TaskView
    created: bool = False
    root_thread_id: str | None = None
    parent_thread_id: str | None = None


type NormalizedLiveEvent = StreamPartEvent | ToolEvent | RunHintEvent | UnknownLiveEvent | TaskChangedEvent


@dataclass(frozen=True, slots=True)
class StartupStarted:
    pass


@dataclass(frozen=True, slots=True)
class StartupReady:
    launch: LaunchProjectResolution
    thread_activity: ThreadActivityPage
    explicit_thread_id: str | None = None


@dataclass(frozen=True, slots=True)
class StartupFailed:
    failure: FailureView


@dataclass(frozen=True, slots=True)
class ClosingStarted:
    pass


@dataclass(frozen=True, slots=True)
class ThreadActivityLoaded:
    request_version: int
    page: ThreadActivityPage
    query: str = ""
    append: bool = False


@dataclass(frozen=True, slots=True)
class ProjectsLoaded:
    request_version: int
    projects: tuple[ProjectSummary, ...]


@dataclass(frozen=True, slots=True)
class SelectorsLoaded:
    request_version: int
    selectors: ThreadSelectorCatalog


@dataclass(frozen=True, slots=True)
class SkillCatalogLoaded:
    request_version: int
    catalog: SkillCatalogView


@dataclass(frozen=True, slots=True)
class ThreadPickerLoaded:
    request_version: int
    page: ThreadActivityPage
    query: str = ""
    append: bool = False


@dataclass(frozen=True, slots=True)
class CompletionLoaded:
    completion: CompletionState


@dataclass(frozen=True, slots=True)
class CompletionClosed:
    pass


@dataclass(frozen=True, slots=True)
class CompletionApplied:
    key: str
    token_start: int
    token_end: int
    replacement: str
    skill_reference: SkillReference | None = None
    project_path: str | None = None


@dataclass(frozen=True, slots=True)
class FocusLoaded:
    request_version: int
    snapshot: ThreadFocusSnapshot
    decisions: DecisionBatchView | None = None


@dataclass(frozen=True, slots=True)
class TranscriptLoaded:
    request_version: int
    thread_id: str
    page: TranscriptPage
    prepend: bool = False


@dataclass(frozen=True, slots=True)
class LiveReceived:
    event: NormalizedLiveEvent


@dataclass(frozen=True, slots=True)
class LiveUnavailable:
    thread_id: str
    failure: FailureView


@dataclass(frozen=True, slots=True)
class RootReceiptAccepted:
    receipt: RootRunReceipt
    draft_key: str
    submitted_text: str
    steering: bool = False
    echo: bool = True
    clear_draft: bool = True


@dataclass(frozen=True, slots=True)
class RootOperationUpdated:
    operation: RootOperationView


@dataclass(frozen=True, slots=True)
class RootControlCompleted:
    result: RootControlResult
    action: Literal["steer", "cancel"]
    draft_key: str | None = None


@dataclass(frozen=True, slots=True)
class ChildControlCompleted:
    parent_thread_id: str
    result: ChildControlResult
    action: Literal["steer", "cancel"]


@dataclass(frozen=True, slots=True)
class DecisionDraftUpdated:
    thread_id: str
    draft: DecisionAnswerDraft


@dataclass(frozen=True, slots=True)
class DecisionPositionChanged:
    thread_id: str
    request_index: int
    question_index: int


@dataclass(frozen=True, slots=True)
class DecisionValidationFailed:
    thread_id: str
    message: str


@dataclass(frozen=True, slots=True)
class DecisionSubmitted:
    thread_id: str
    receipt_id: str


@dataclass(frozen=True, slots=True)
class ReviewLoaded:
    request_version: int
    key: str
    view: ReviewView
    thread_id: str | None = None
    execution_id: str | None = None
    available_actions: tuple[Literal["wait", "steer", "cancel"], ...] = ()


@dataclass(frozen=True, slots=True)
class DisclosureChanged:
    kind: Literal["reasoning", "tools"]


@dataclass(frozen=True, slots=True)
class DraftDefaultsChanged:
    defaults: NewThreadDefaults


@dataclass(frozen=True, slots=True)
class DraftChanged:
    key: str
    text: str
    cursor: int
    project_paths: tuple[str, ...] = ()
    skill_references: tuple[SkillReference, ...] = ()


@dataclass(frozen=True, slots=True)
class DraftRestored:
    key: str
    text: str
    expected_revision: int
    message: str
    code: str | None = None


@dataclass(frozen=True, slots=True)
class EditorDraftApplied:
    key: str
    text: str
    expected_revision: int


@dataclass(frozen=True, slots=True)
class DraftRekeyed:
    old_key: str
    new_key: str


@dataclass(frozen=True, slots=True)
class DraftSubmitted:
    key: str


@dataclass(frozen=True, slots=True)
class RouteChanged:
    thread_id: str | None = None
    new_draft: bool = False
    clear_focus: bool = False


@dataclass(frozen=True, slots=True)
class OverlayOpened:
    overlay: OverlayState


@dataclass(frozen=True, slots=True)
class OverlayClosed:
    pass


@dataclass(frozen=True, slots=True)
class ConfigurationConflictRecorded:
    conflict: ConfigurationConflictState


@dataclass(frozen=True, slots=True)
class ConfigurationConflictCleared:
    pass


@dataclass(frozen=True, slots=True)
class FollowLatestChanged:
    thread_id: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class ReadingAnchorChanged:
    thread_id: str
    anchor: ReadingAnchor | None


@dataclass(frozen=True, slots=True)
class TimelineSelectionChanged:
    thread_id: str
    block_id: str | None


@dataclass(frozen=True, slots=True)
class OperationFailed:
    action: str
    failure: FailureView
    draft_key: str | None = None
    draft_text: str | None = None
    thread_id: str | None = None


type TerminalEvent = (
    StartupStarted
    | StartupReady
    | StartupFailed
    | ClosingStarted
    | ThreadActivityLoaded
    | ProjectsLoaded
    | SelectorsLoaded
    | SkillCatalogLoaded
    | ThreadPickerLoaded
    | CompletionLoaded
    | CompletionClosed
    | CompletionApplied
    | FocusLoaded
    | TranscriptLoaded
    | LiveReceived
    | LiveUnavailable
    | RootReceiptAccepted
    | RootOperationUpdated
    | RootControlCompleted
    | ChildControlCompleted
    | DecisionDraftUpdated
    | DecisionPositionChanged
    | DecisionValidationFailed
    | DecisionSubmitted
    | ReviewLoaded
    | DisclosureChanged
    | DraftDefaultsChanged
    | DraftChanged
    | DraftRestored
    | EditorDraftApplied
    | DraftRekeyed
    | DraftSubmitted
    | RouteChanged
    | OverlayOpened
    | OverlayClosed
    | ConfigurationConflictRecorded
    | ConfigurationConflictCleared
    | FollowLatestChanged
    | ReadingAnchorChanged
    | TimelineSelectionChanged
    | OperationFailed
)


def normalize_live_event(event: LiveEvent) -> NormalizedLiveEvent:
    """Map one bounded AG-UI envelope to a closed terminal event union."""

    payload = event.payload or {}
    event_type = event.event_type.upper()
    execution_id = event.execution_id
    common = {
        "epoch": event.epoch,
        "sequence": event.sequence,
        "thread_id": event.thread_id,
        "run_id": event.run_id,
        "execution_id": execution_id,
        "root_thread_id": event.root_thread_id,
        "parent_thread_id": event.parent_thread_id,
    }
    if event.payload_omitted:
        return UnknownLiveEvent(
            **common,
            event_type=event.event_type,
            summary=f"{event.event_type} payload omitted",
        )

    stream_kind: Literal["assistant", "reasoning"] | None = None
    if event_type.startswith("TEXT_MESSAGE"):
        stream_kind = "assistant"
    elif event_type.startswith("REASONING_MESSAGE") or event_type.startswith("THINKING_TEXT_MESSAGE"):
        stream_kind = "reasoning"
    if stream_kind is not None:
        message_id = _string(payload, "message_id") or f"sequence-{event.sequence}"
        if event_type.endswith("START"):
            action: Literal["open", "append", "close"] = "open"
        elif event_type.endswith("END"):
            action = "close"
        else:
            action = "append"
        return StreamPartEvent(
            **common,
            part_id=message_id,
            kind=stream_kind,
            action=action,
            text=_string(payload, "delta") or _string(payload, "content") or "",
        )

    if event_type.startswith("TOOL_CALL"):
        tool_call_id = _string(payload, "tool_call_id") or f"sequence-{event.sequence}"
        if event_type.endswith("START"):
            tool_action: Literal["open", "arguments", "close", "result"] = "open"
        elif event_type.endswith("ARGS") or event_type.endswith("CHUNK"):
            tool_action = "arguments"
        elif event_type.endswith("RESULT"):
            tool_action = "result"
        else:
            tool_action = "close"
        return ToolEvent(
            **common,
            tool_call_id=tool_call_id,
            action=tool_action,
            tool_name=_string(payload, "tool_call_name") or _string(payload, "name"),
            text=_string(payload, "delta") or _string(payload, "content") or "",
        )

    if event_type in {"RUN_STARTED", "RUN_FINISHED", "RUN_ERROR"}:
        if event_type == "RUN_STARTED":
            run_action: Literal["started", "finished", "error"] = "started"
        elif event_type == "RUN_FINISHED":
            run_action = "finished"
        else:
            run_action = "error"
        return RunHintEvent(
            **common,
            action=run_action,
            message=_string(payload, "message") or _string(payload, "code"),
        )

    name = _string(payload, "name") if event_type == "CUSTOM" else None
    summary = name or event.event_type
    value = payload.get("value")
    if name == "a13n.harness.state" and isinstance(value, dict):
        extension = value.get("event")
        mutation = extension.get("payload") if isinstance(extension, dict) else None
        if isinstance(mutation, dict) and mutation.get("type") == "task_changed":
            task = mutation.get("task")
            version = mutation.get("task_state_version")
            if isinstance(task, dict) and isinstance(version, int) and not isinstance(version, bool) and version >= 1:
                try:
                    projected = TaskView.model_validate(
                        {
                            "task_id": task.get("id"),
                            **{key: task[key] for key in TaskView.model_fields if key != "task_id" and key in task},
                        }
                    )
                except ValidationError:
                    pass
                else:
                    return TaskChangedEvent(
                        **common,
                        task_state_version=version,
                        task=projected,
                        created=mutation.get("reason") == "created",
                    )
    if value is not None:
        try:
            rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            rendered = ""
        if rendered:
            summary = f"{summary}: {rendered[:512]}"
    return UnknownLiveEvent(
        **common,
        event_type=event.event_type,
        summary=summary[:1024],
    )


def _string(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


__all__ = [
    "ChildControlCompleted",
    "ClosingStarted",
    "CompletionApplied",
    "CompletionClosed",
    "CompletionLoaded",
    "ConfigurationConflictCleared",
    "ConfigurationConflictRecorded",
    "DecisionDraftUpdated",
    "DecisionPositionChanged",
    "DecisionSubmitted",
    "DecisionValidationFailed",
    "DisclosureChanged",
    "DraftChanged",
    "DraftDefaultsChanged",
    "DraftRekeyed",
    "DraftRestored",
    "DraftSubmitted",
    "EditorDraftApplied",
    "FocusLoaded",
    "FollowLatestChanged",
    "LiveReceived",
    "LiveUnavailable",
    "NormalizedLiveEvent",
    "OperationFailed",
    "OverlayClosed",
    "OverlayOpened",
    "ProjectsLoaded",
    "ReadingAnchorChanged",
    "ReviewLoaded",
    "RootControlCompleted",
    "RootOperationUpdated",
    "RootReceiptAccepted",
    "RouteChanged",
    "RunHintEvent",
    "SelectorsLoaded",
    "SkillCatalogLoaded",
    "StartupFailed",
    "StartupReady",
    "StartupStarted",
    "StreamPartEvent",
    "TaskChangedEvent",
    "TerminalEvent",
    "ThreadActivityLoaded",
    "ThreadPickerLoaded",
    "TimelineSelectionChanged",
    "ToolEvent",
    "TranscriptLoaded",
    "UnknownLiveEvent",
    "normalize_live_event",
]
