"""Transient per-tab workbench awareness, independent of drafts and execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Annotated, Literal, Self
from uuid import uuid4

from anyio import Event
from pydantic import Field, model_validator

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.host_files import NativePath
from a13n_harness_ui.surfaces import SurfaceModel

PRESENCE_TIMEOUT_SECONDS = 60
PRESENCE_REFRESH_SECONDS = 15
MAX_PARTICIPANTS = 32
POINTER_TIMEOUT_SECONDS = 5


class WorkbenchPage(SurfaceModel):
    kind: Literal["workbench"] = "workbench"
    section: Literal["home", "settings", "catalog"] = "home"


class ConversationPage(SurfaceModel):
    kind: Literal["conversation"] = "conversation"
    thread_id: str = Field(min_length=1, max_length=80)


class ProjectPage(SurfaceModel):
    kind: Literal["project"] = "project"
    project_id: str = Field(min_length=1, max_length=128)


class ResourcePage(SurfaceModel):
    kind: Literal["resource"] = "resource"
    resource_kind: Literal[
        "model",
        "agent",
        "subagent",
        "harness_plugin",
        "environment_profile",
        "environment_run_extension",
        "mcp_server",
        "content_plugin",
    ]
    resource_id: str = Field(min_length=1, max_length=128)


class FilePage(SurfaceModel):
    kind: Literal["file"] = "file"
    path: NativePath


class ChangesPage(SurfaceModel):
    kind: Literal["changes"] = "changes"
    repository_root: NativePath
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    comparison: Literal["staged", "unstaged", "untracked"] | None = None


class TerminalPage(SurfaceModel):
    kind: Literal["terminal"] = "terminal"
    terminal_id: str = Field(min_length=1, max_length=80)


type PageTarget = Annotated[
    WorkbenchPage | ConversationPage | ProjectPage | ResourcePage | FilePage | ChangesPage | TerminalPage,
    Field(discriminator="kind"),
]


class PageFocus(SurfaceModel):
    target: PageTarget
    root_thread_id: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def _thread_context(self) -> Self:
        if isinstance(self.target, ConversationPage):
            if self.root_thread_id is not None and self.root_thread_id != self.target.thread_id:
                raise ValueError("conversation focus must retain its own root Thread context")
        return self


class PresenceReport(SurfaceModel):
    kind: Literal["presence"] = "presence"
    display_name: str = Field(default="", max_length=80)
    color: str = Field(default="#64748b", pattern=r"^#[0-9a-fA-F]{6}$")
    focus: PageFocus | None = None
    foreground: bool = False
    pointer_enabled: bool = False


class ParticipantPresence(PresenceReport):
    participant_id: str
    availability: Literal["available", "unavailable", "unknown"] = "unknown"
    unavailable_reason: str | None = None


class PresenceFrame(SurfaceModel):
    kind: Literal["presence"] = "presence"
    participant_id: str | None = None
    participants: tuple[ParticipantPresence, ...]
    same_page_participant_ids: tuple[str, ...] = ()
    closed: bool = False


class PointerPosition(SurfaceModel):
    anchor: str = Field(min_length=1, max_length=256)
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)


class PointerReport(SurfaceModel):
    kind: Literal["pointer"] = "pointer"
    target: ConversationPage
    pointer: PointerPosition | None = None


class PointerFrame(SurfaceModel):
    kind: Literal["pointers"] = "pointers"
    target: ConversationPage | None = None
    pointers: dict[str, PointerPosition] = Field(default_factory=dict)


@dataclass
class _PendingProjection:
    revision: Event
    ready: Event = field(default_factory=Event)
    participants: tuple[ParticipantPresence, ...] | None = None


class PagePresence:
    """Only live membership; every reconnect allocates a fresh tab identity."""

    def __init__(self) -> None:
        self.participants: dict[str, PresenceReport] = {}
        self.changed = Event()
        self.pointer_changed = Event()
        self.pointers: dict[str, tuple[PointerPosition, float]] = {}
        self.closed = False
        self._projection: _PendingProjection | None = None

    def _notify(self) -> None:
        self.changed.set()
        self.changed = Event()

    def _notify_pointers(self) -> None:
        self.pointer_changed.set()
        self.pointer_changed = Event()

    def attach(self) -> str:
        if self.closed or len(self.participants) >= MAX_PARTICIPANTS:
            raise HarnessUiError("Page presence is closed or full.", code="presence_unavailable")
        identity = f"participant-{uuid4().hex}"
        self.participants[identity] = PresenceReport()
        self._notify()
        return identity

    def report(self, identity: str, report: PresenceReport) -> None:
        if self.closed or identity not in self.participants:
            raise HarnessUiError("Page participation has ended.", code="presence_instance_conflict")
        if len(report.model_dump_json().encode("utf-8")) > 16 * 1024:
            raise ValueError("Presence report exceeds 16 KiB.")
        previous = self.participants[identity]
        if previous != report:
            self.participants[identity] = report
            if previous.focus != report.focus or not report.foreground or not report.pointer_enabled:
                self.pointers.pop(identity, None)
            self._notify_pointers()
            self._notify()

    def report_pointer(self, identity: str, report: PointerReport) -> None:
        own = self.participants.get(identity)
        if self.closed or own is None:
            raise HarnessUiError("Page participation has ended.", code="presence_instance_conflict")
        if not own.pointer_enabled or not own.foreground or own.focus is None or own.focus.target != report.target:
            return
        if report.pointer is None:
            self.pointers.pop(identity, None)
        else:
            self.pointers[identity] = (report.pointer, monotonic())
        self._notify_pointers()

    def pointer_snapshot(self, identity: str) -> PointerFrame:
        # High-frequency observations never inspect resources or rebuild the directory.
        now = monotonic()
        self.pointers = {key: value for key, value in self.pointers.items() if now - value[1] < POINTER_TIMEOUT_SECONDS}
        own = self.participants.get(identity)
        if own is None or own.focus is None or not isinstance(own.focus.target, ConversationPage):
            return PointerFrame()
        target = own.focus.target
        pointers = {
            key: position
            for key, (position, _) in self.pointers.items()
            if key != identity
            and own.foreground
            and (peer := self.participants.get(key)) is not None
            and peer.foreground
            and peer.focus is not None
            and peer.focus.target == target
        }
        return PointerFrame(target=target, pointers=pointers)

    def detach(self, identity: str) -> None:
        self.participants.pop(identity, None)
        self.pointers.pop(identity, None)
        self._notify_pointers()
        self._notify()

    async def snapshot(
        self, identity: str | None, availability: Callable[[PageFocus], Awaitable[str | None]]
    ) -> PresenceFrame:
        participants = await self._project(availability)
        if self.closed:
            return PresenceFrame(participant_id=identity, participants=(), closed=True)
        own = next((item for item in participants if item.participant_id == identity), None)
        return PresenceFrame(
            participant_id=identity,
            participants=participants,
            same_page_participant_ids=tuple(
                item.participant_id
                for item in participants
                if item.participant_id != identity
                and own is not None
                and own.focus is not None
                and item.focus is not None
                and own.focus.target == item.focus.target
            ),
        )

    async def _project(
        self, availability: Callable[[PageFocus], Awaitable[str | None]]
    ) -> tuple[ParticipantPresence, ...]:
        # Share only in-flight work for the same membership revision. A later
        # snapshot rechecks resources even if no tab has navigated (e.g. a file
        # was deleted). Cancellation must wake followers without stranding them.
        while True:
            pending = self._projection
            if pending is not None and pending.revision is self.changed:
                await pending.ready.wait()
                if pending.participants is not None:
                    return pending.participants
                continue
            pending = _PendingProjection(self.changed)
            self._projection = pending
            try:
                pending.participants = await self._inspect(tuple(self.participants.items()), availability)
                return pending.participants
            finally:
                if self._projection is pending:
                    self._projection = None
                pending.ready.set()

    @staticmethod
    async def _inspect(
        reports: tuple[tuple[str, PresenceReport], ...],
        availability: Callable[[PageFocus], Awaitable[str | None]],
    ) -> tuple[ParticipantPresence, ...]:
        # Detached before I/O; no DB transaction or membership lock spans it.
        participants: list[ParticipantPresence] = []
        checked: dict[str, str | None] = {}
        for participant, report in reports:
            reason = None
            if report.focus is not None:
                key = report.focus.model_dump_json()
                if key not in checked:
                    checked[key] = await availability(report.focus)
                reason = checked[key]
            participants.append(
                ParticipantPresence(
                    **report.model_dump(),
                    participant_id=participant,
                    availability="unknown" if report.focus is None else "unavailable" if reason else "available",
                    unavailable_reason=reason,
                )
            )
        return tuple(participants)

    def close(self) -> None:
        self.closed = True
        self.participants.clear()
        self.pointers.clear()
        self._notify_pointers()
        self._notify()
