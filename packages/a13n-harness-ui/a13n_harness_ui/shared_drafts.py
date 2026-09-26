"""Process-local Y CRDT composer state; synchronization never submits input."""

from __future__ import annotations

import base64
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Literal
from uuid import uuid4

from anyio import Event, Lock
from pycrdt import Doc, Map, Text
from pydantic import Field

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.surfaces import SurfaceModel

MAX_DRAFT_BYTES = 512 * 1024
DRAFT_PRESENCE_TIMEOUT_SECONDS = 30
_INLINE_ATTACHMENT = re.compile(r"\ufffc(inline-[0-9a-f-]{36})\ufffc")


class DraftSummary(SurfaceModel):
    thread_id: str
    draft_id: str
    unsent_since: datetime


class DraftPresence(SurfaceModel):
    name: str = Field(default="", max_length=80)
    color: str = Field(default="#64748b", pattern=r"^#[0-9a-fA-F]{6}$")
    # Opaque base64 Y relative positions, not offsets tied to stale text.
    anchor: str | None = Field(default=None, max_length=1024)
    head: str | None = Field(default=None, max_length=1024)


class DraftCommand(SurfaceModel):
    kind: Literal["sync", "presence"]
    draft_id: str = Field(max_length=100)
    # Complete Yjs v1 update, including dependencies and deletion sets.
    # Full updates keep rejoin and rejected-update recovery stateless.
    update_base64: str | None = Field(default=None, max_length=700000)
    presence: DraftPresence | None = None


class DraftFrame(SurfaceModel):
    kind: Literal["draft"] = "draft"
    draft_id: str
    participant_id: str
    update_base64: str
    participants: dict[str, DraftPresence]
    closed: bool = False


def composer_document() -> Doc:
    return Doc({"text": Text(), "attachments": Map[str]()})


def composer_values(document: Doc) -> tuple[str, tuple[str, ...]]:
    if set(document.keys()) != {"text", "attachments"}:
        raise ValueError("A composer has only text and attachments roots.")
    text = document.get("text", type=Text)
    attachments = document.get("attachments", type=Map[str])
    if any(not isinstance(value, str) or attributes for value, attributes in text.diff()):
        raise ValueError("Composer text must be plain text.")
    prompt = str(text)
    if len(prompt) > 256 * 1024 or "\x00" in prompt:
        raise ValueError("Composer text exceeds its limit or contains NUL.")
    values = attachments.to_py()
    if any(
        not isinstance(key, str) or not 1 <= len(key) <= 100 or not isinstance(value, str) or not 1 <= len(value) <= 100
        for key, value in values.items()
    ):
        raise ValueError("A composer supports up to eight Thread attachment references.")
    # Inline registry entries survive text deletion for native undo/redo. Only
    # live tokens select input; dormant entries remain under the full CRDT cap.
    keys = [match[1] for match in _INLINE_ATTACHMENT.finditer(prompt)]
    if len(keys) > 8:
        raise ValueError("A composer supports up to eight Thread attachment references.")
    return prompt, tuple(values[key] for key in keys if key in values and values[key] not in {"pending", "failed"})


class SharedDraft:
    def __init__(self) -> None:
        self.draft_id = f"draft-{uuid4().hex}"
        self.document = composer_document()
        self.unsent_since: datetime | None = None
        self.participants: dict[str, DraftPresence] = {}
        self._presence_updated: dict[str, float] = {}
        self.changed = Event()
        self.closed = False
        self._lock = Lock()

    def _notify(self) -> None:
        self.changed.set()
        self.changed = Event()

    def attach(self) -> str:
        participant = f"participant-{uuid4().hex}"
        self.participants[participant] = DraftPresence()
        self._notify()
        return participant

    def detach(self, participant: str) -> None:
        self.participants.pop(participant, None)
        self._presence_updated.pop(participant, None)
        self._notify()

    def expire_presence(self) -> None:
        now = monotonic()
        expired = [
            key for key, updated in self._presence_updated.items() if now - updated >= DRAFT_PRESENCE_TIMEOUT_SECONDS
        ]
        for key in expired:
            self._presence_updated.pop(key)
            if key in self.participants:
                self.participants[key] = self.participants[key].model_copy(update={"anchor": None, "head": None})
        if expired:
            self._notify()

    def frame(self, participant: str) -> DraftFrame:
        self.expire_presence()
        return DraftFrame(
            draft_id=self.draft_id,
            participant_id=participant,
            update_base64=base64.b64encode(self.document.get_update()).decode("ascii"),
            participants=dict(self.participants),
            closed=self.closed,
        )

    async def command(
        self,
        participant: str,
        command: DraftCommand,
        validate_attachments: Callable[[tuple[str, ...]], Awaitable[None]],
    ) -> bool:
        """Apply an edit and report a change in nonempty draft membership."""
        async with self._lock:
            was_unsent = self.unsent_since is not None
            if self.closed or participant not in self.participants or command.draft_id != self.draft_id:
                raise HarnessUiError("The shared draft instance changed.", code="draft_instance_conflict")
            if command.kind == "presence":
                if command.presence is None or command.update_base64 is not None:
                    raise ValueError("Presence requires only participant presentation.")
                self.participants[participant] = command.presence
                if command.presence.anchor is not None and command.presence.head is not None:
                    self._presence_updated[participant] = monotonic()
                else:
                    self._presence_updated.pop(participant, None)
            else:
                if command.update_base64 is None or command.presence is not None:
                    raise ValueError("Sync requires a complete document update.")
                update = base64.b64decode(command.update_base64, validate=True)
                if len(update) > MAX_DRAFT_BYTES:
                    raise ValueError("Shared draft exceeds 512 KiB of CRDT state.")
                candidate = composer_document()
                candidate.apply_update(self.document.get_update())
                candidate.apply_update(update)
                _, attachments = composer_values(candidate)
                if len(candidate.get_update()) > MAX_DRAFT_BYTES:
                    raise ValueError("Shared draft exceeds 512 KiB of CRDT state.")
                await validate_attachments(attachments)
                # Publish only after the entire composer has validated. Rejected
                # selections cannot partially replace the shared text.
                self.document = candidate
                text = str(candidate.get("text", type=Text))
                # Live tokens count even during uploads; dormant entries do not.
                nonempty = bool(text.strip())
                if not nonempty:
                    self.unsent_since = None
                elif self.unsent_since is None:
                    self.unsent_since = datetime.now(UTC)
            self._notify()
            return was_unsent != (self.unsent_since is not None)

    def close(self) -> None:
        self.closed = True
        self.participants.clear()
        self._presence_updated.clear()
        self._notify()
