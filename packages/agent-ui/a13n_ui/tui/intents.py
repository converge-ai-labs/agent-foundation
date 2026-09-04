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


@dataclass(frozen=True, slots=True)
class RetryStartup:
    pass


@dataclass(frozen=True, slots=True)
class ExitTerminal:
    pass


@dataclass(frozen=True, slots=True)
class ToggleTopLevelMode:
    pass


@dataclass(frozen=True, slots=True)
class OpenWorkbench:
    pass


@dataclass(frozen=True, slots=True)
class OpenFocus:
    thread_id: str


@dataclass(frozen=True, slots=True)
class StartNewDraft:
    defaults: NewThreadDefaults | None = None


@dataclass(frozen=True, slots=True)
class SelectWorkbenchThread:
    thread_id: str


@dataclass(frozen=True, slots=True)
class SetWorkbenchFilter:
    project_id: str | None


@dataclass(frozen=True, slots=True)
class SearchWorkbench:
    query: str


@dataclass(frozen=True, slots=True)
class LoadOlderTranscript:
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
class SubmitDecisions:
    thread_id: str
    response: DecisionResponseBatch


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
        "review",
        "inspector",
    ]
    key: str | None = None


@dataclass(frozen=True, slots=True)
class CloseOverlay:
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
class AcknowledgeWorkbenchCompletion:
    receipt_id: str


@dataclass(frozen=True, slots=True)
class OpenExternalEditor:
    key: str


type TerminalIntent = (
    RetryStartup
    | ExitTerminal
    | ToggleTopLevelMode
    | OpenWorkbench
    | OpenFocus
    | StartNewDraft
    | SelectWorkbenchThread
    | SetWorkbenchFilter
    | SearchWorkbench
    | LoadOlderTranscript
    | EditDraft
    | SubmitComposer
    | CancelFocusedOperation
    | SubmitDecisions
    | PatchThreadConfiguration
    | ArchiveThread
    | OpenOverlay
    | CloseOverlay
    | SelectTimelineBlock
    | SetFollowLatest
    | SetReadingAnchor
    | AcknowledgeWorkbenchCompletion
    | OpenExternalEditor
)


__all__ = [
    "AcknowledgeWorkbenchCompletion",
    "ArchiveThread",
    "CancelFocusedOperation",
    "CloseOverlay",
    "EditDraft",
    "ExitTerminal",
    "LoadOlderTranscript",
    "OpenExternalEditor",
    "OpenFocus",
    "OpenOverlay",
    "OpenWorkbench",
    "PatchThreadConfiguration",
    "RetryStartup",
    "SearchWorkbench",
    "SelectTimelineBlock",
    "SelectWorkbenchThread",
    "SetFollowLatest",
    "SetReadingAnchor",
    "SetWorkbenchFilter",
    "StartNewDraft",
    "SubmitComposer",
    "SubmitDecisions",
    "TerminalIntent",
    "ToggleTopLevelMode",
]
