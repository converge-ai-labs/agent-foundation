"""Normalized App facts consumed by the pure terminal reducer."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from a13n_ui.live import LiveEvent
from a13n_ui.surfaces import (
    DecisionBatchView,
    FailureView,
    LaunchProjectResolution,
    RootControlResult,
    RootOperationView,
    RootRunReceipt,
    SkillReference,
    ThreadFocusSnapshot,
    TranscriptPage,
    WorkbenchPage,
)
from a13n_ui.tui.models import OverlayState, ReadingAnchor


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


@dataclass(frozen=True, slots=True)
class RunHintEvent:
    epoch: str
    sequence: int
    thread_id: str
    run_id: str
    execution_id: str | None
    action: Literal["started", "finished", "error"]
    message: str | None = None


@dataclass(frozen=True, slots=True)
class UnknownLiveEvent:
    epoch: str
    sequence: int
    thread_id: str
    run_id: str
    execution_id: str | None
    event_type: str
    summary: str


type NormalizedLiveEvent = StreamPartEvent | ToolEvent | RunHintEvent | UnknownLiveEvent


@dataclass(frozen=True, slots=True)
class StartupReady:
    launch: LaunchProjectResolution
    workbench: WorkbenchPage
    explicit_thread_id: str | None = None
    open_workbench: bool = False


@dataclass(frozen=True, slots=True)
class StartupFailed:
    failure: FailureView


@dataclass(frozen=True, slots=True)
class ClosingStarted:
    pass


@dataclass(frozen=True, slots=True)
class WorkbenchLoaded:
    request_version: int
    page: WorkbenchPage


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


@dataclass(frozen=True, slots=True)
class RootOperationUpdated:
    operation: RootOperationView


@dataclass(frozen=True, slots=True)
class RootControlCompleted:
    result: RootControlResult
    action: Literal["steer", "cancel"]
    draft_key: str | None = None


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
    message: str
    code: str | None = None


@dataclass(frozen=True, slots=True)
class RouteChanged:
    mode: Literal["focus", "workbench"]
    thread_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorkbenchSelectionChanged:
    thread_id: str | None


@dataclass(frozen=True, slots=True)
class OverlayOpened:
    overlay: OverlayState


@dataclass(frozen=True, slots=True)
class OverlayClosed:
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
class CompletionAcknowledged:
    receipt_id: str


@dataclass(frozen=True, slots=True)
class OperationFailed:
    action: str
    failure: FailureView
    draft_key: str | None = None
    draft_text: str | None = None
    thread_id: str | None = None


type TerminalEvent = (
    StartupReady
    | StartupFailed
    | ClosingStarted
    | WorkbenchLoaded
    | FocusLoaded
    | TranscriptLoaded
    | LiveReceived
    | LiveUnavailable
    | RootReceiptAccepted
    | RootOperationUpdated
    | RootControlCompleted
    | DraftChanged
    | DraftRestored
    | RouteChanged
    | WorkbenchSelectionChanged
    | OverlayOpened
    | OverlayClosed
    | FollowLatestChanged
    | ReadingAnchorChanged
    | TimelineSelectionChanged
    | CompletionAcknowledged
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
    "ClosingStarted",
    "CompletionAcknowledged",
    "DraftChanged",
    "DraftRestored",
    "FocusLoaded",
    "FollowLatestChanged",
    "LiveReceived",
    "LiveUnavailable",
    "NormalizedLiveEvent",
    "OperationFailed",
    "OverlayClosed",
    "OverlayOpened",
    "ReadingAnchorChanged",
    "RootControlCompleted",
    "RootOperationUpdated",
    "RootReceiptAccepted",
    "RouteChanged",
    "RunHintEvent",
    "StartupFailed",
    "StartupReady",
    "StreamPartEvent",
    "TerminalEvent",
    "TimelineSelectionChanged",
    "ToolEvent",
    "TranscriptLoaded",
    "UnknownLiveEvent",
    "WorkbenchLoaded",
    "WorkbenchSelectionChanged",
    "normalize_live_event",
]
