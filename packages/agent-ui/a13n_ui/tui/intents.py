"""Typed user intents emitted by terminal screens and widgets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from a13n_ui.surfaces import (
    DecisionResponseBatch,
    NewThreadDefaults,
    SkillReference,
    ThreadConfigurationMutationInput,
)
from a13n_ui.tui.models import ConfigurationResourceKind, DecisionAnswerDraft


@dataclass(frozen=True, slots=True)
class RetryStartup:
    pass


@dataclass(frozen=True, slots=True)
class ExitTerminal:
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class ExecuteCommand:
    name: str
    draft_key: str | None = None
    context_key: str | None = None


@dataclass(frozen=True, slots=True)
class OpenFocus:
    thread_id: str


@dataclass(frozen=True, slots=True)
class StartNewDraft:
    defaults: NewThreadDefaults | None = None


@dataclass(frozen=True, slots=True)
class SetThreadFilter:
    project_id: str | None


@dataclass(frozen=True, slots=True)
class SearchThreadPicker:
    query: str


@dataclass(frozen=True, slots=True)
class LoadMoreThreadPicker:
    pass


@dataclass(frozen=True, slots=True)
class LoadOlderTranscript:
    thread_id: str


@dataclass(frozen=True, slots=True)
class LoadLatestTranscript:
    thread_id: str


@dataclass(frozen=True, slots=True)
class EditDraft:
    key: str
    text: str
    cursor: int
    skill_references: tuple[SkillReference, ...] = ()
    project_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SubmitComposer:
    key: str


@dataclass(frozen=True, slots=True)
class CancelFocusedOperation:
    pass


@dataclass(frozen=True, slots=True)
class SteerChildExecution:
    parent_thread_id: str
    execution_id: str
    message: str


@dataclass(frozen=True, slots=True)
class CancelChildExecution:
    parent_thread_id: str
    execution_id: str


@dataclass(frozen=True, slots=True)
class SubmitDecisions:
    thread_id: str
    response: DecisionResponseBatch


@dataclass(frozen=True, slots=True)
class UpdateDecisionDraft:
    thread_id: str
    draft: DecisionAnswerDraft


@dataclass(frozen=True, slots=True)
class NavigateDecision:
    thread_id: str
    request_index: int
    question_index: int = 0


@dataclass(frozen=True, slots=True)
class SubmitDecisionSession:
    thread_id: str


@dataclass(frozen=True, slots=True)
class OpenReview:
    kind: Literal["retained", "deferred", "task", "child"]
    thread_id: str
    continuation_id: str | None = None
    position: int | None = None
    tool_call_id: str | None = None
    request_id: str | None = None
    task_id: str | None = None
    execution_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToggleReasoning:
    pass


@dataclass(frozen=True, slots=True)
class ToggleToolDetails:
    pass


@dataclass(frozen=True, slots=True)
class PatchThreadConfiguration:
    thread_id: str
    mutation: ThreadConfigurationMutationInput


@dataclass(frozen=True, slots=True)
class ArchiveThread:
    thread_id: str
    expected_version: int
    archived: bool = True


@dataclass(frozen=True, slots=True)
class OpenOverlay:
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


@dataclass(frozen=True, slots=True)
class CloseOverlay:
    pass


@dataclass(frozen=True, slots=True)
class SelectConfigurationResource:
    kind: ConfigurationResourceKind
    resource_id: str


@dataclass(frozen=True, slots=True)
class RequestCompletions:
    key: str
    kind: Literal["path", "skill"]
    query: str
    token_start: int
    token_end: int


@dataclass(frozen=True, slots=True)
class ApplyCompletion:
    key: str
    token_start: int
    token_end: int
    replacement: str
    skill_reference: SkillReference | None = None
    project_path: str | None = None


@dataclass(frozen=True, slots=True)
class InsertSkillReference:
    key: str
    reference: SkillReference


@dataclass(frozen=True, slots=True)
class CloseCompletions:
    pass


@dataclass(frozen=True, slots=True)
class SelectTimelineBlock:
    thread_id: str
    block_id: str | None


@dataclass(frozen=True, slots=True)
class SetFollowLatest:
    thread_id: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class SetReadingAnchor:
    thread_id: str
    block_id: str
    line_offset: int = 0


@dataclass(frozen=True, slots=True)
class OpenExternalEditor:
    key: str


type TerminalIntent = (
    RetryStartup
    | ExitTerminal
    | ExecuteCommand
    | OpenFocus
    | StartNewDraft
    | SetThreadFilter
    | LoadMoreThreadPicker
    | SearchThreadPicker
    | LoadOlderTranscript
    | LoadLatestTranscript
    | EditDraft
    | SubmitComposer
    | CancelFocusedOperation
    | SteerChildExecution
    | CancelChildExecution
    | SubmitDecisions
    | UpdateDecisionDraft
    | NavigateDecision
    | SubmitDecisionSession
    | OpenReview
    | ToggleReasoning
    | ToggleToolDetails
    | PatchThreadConfiguration
    | ArchiveThread
    | OpenOverlay
    | CloseOverlay
    | SelectConfigurationResource
    | RequestCompletions
    | ApplyCompletion
    | InsertSkillReference
    | CloseCompletions
    | SelectTimelineBlock
    | SetFollowLatest
    | SetReadingAnchor
    | OpenExternalEditor
)


__all__ = [
    "ApplyCompletion",
    "ArchiveThread",
    "CancelChildExecution",
    "CancelFocusedOperation",
    "CloseCompletions",
    "CloseOverlay",
    "EditDraft",
    "ExecuteCommand",
    "ExitTerminal",
    "InsertSkillReference",
    "LoadLatestTranscript",
    "LoadOlderTranscript",
    "NavigateDecision",
    "OpenExternalEditor",
    "OpenFocus",
    "OpenOverlay",
    "OpenReview",
    "PatchThreadConfiguration",
    "RequestCompletions",
    "RetryStartup",
    "SearchThreadPicker",
    "SelectConfigurationResource",
    "SelectTimelineBlock",
    "SetFollowLatest",
    "SetReadingAnchor",
    "SetThreadFilter",
    "StartNewDraft",
    "SteerChildExecution",
    "SubmitComposer",
    "SubmitDecisionSession",
    "SubmitDecisions",
    "TerminalIntent",
    "ToggleReasoning",
    "ToggleToolDetails",
    "UpdateDecisionDraft",
]
