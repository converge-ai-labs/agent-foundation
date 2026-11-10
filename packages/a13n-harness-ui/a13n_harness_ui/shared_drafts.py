"""Validated, persistent Y CRDT composer state; synchronization never submits input."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Literal
from uuid import uuid4

from anyio import Event, Lock
from pycrdt import Doc, Map, Text
from pydantic import Field

from a13n_harness_ui.environment_bindings import EnvironmentSelectionPatch
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.storage.drafts import StoredDraft
from a13n_harness_ui.surfaces import RunModelOverrides, SurfaceModel

MAX_DRAFT_BYTES = 512 * 1024
DRAFT_PRESENCE_TIMEOUT_SECONDS = 30
_INLINE_ATTACHMENT = re.compile(r"\ufffc(inline-[0-9a-f-]{36})\ufffc")


class DraftSummary(SurfaceModel):
    thread_id: str
    draft_id: str
    unsent_since: datetime


class DraftCapture(SurfaceModel):
    """Exact synchronized input to consume only after submission is accepted."""

    draft_id: str = Field(min_length=1, max_length=100)
    update_base64: str = Field(min_length=1, max_length=700000)
    options_update_base64: str | None = Field(default=None, max_length=700000)


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
    options_update_base64: str | None = Field(default=None, max_length=700000)
    presence: DraftPresence | None = None


class DraftFrame(SurfaceModel):
    kind: Literal["draft"] = "draft"
    draft_id: str
    participant_id: str
    update_base64: str
    options_update_base64: str | None = None
    participants: dict[str, DraftPresence]
    closed: bool = False


class ComposerModelOptions(RunModelOverrides):
    # Bind retained model controls to the Agent and effective Model they describe.
    agent: str | None = Field(default=None, min_length=1, max_length=128)
    effective_model: str | None = Field(default=None, min_length=1, max_length=128)


class ComposerOptions(SurfaceModel):
    model: ComposerModelOptions | None = None
    environment: EnvironmentSelectionPatch | None = None
    mode: Literal["normal", "goal"] = "normal"


def composer_document() -> Doc:
    return Doc({"text": Text(), "attachments": Map[str]()})


def options_document() -> Doc:
    return Doc({"options": Map()})


def composer_options(document: Doc) -> ComposerOptions:
    if set(document.keys()) != {"options"}:
        raise ValueError("Composer options have only an options root.")
    # JSON decoding preserves strict wire validation and tuple-valued environment axes.
    return ComposerOptions.model_validate_json(json.dumps(document.get("options", type=Map).to_py()))


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
    def __init__(
        self,
        *,
        thread_id: str = "",
        stored: StoredDraft | None = None,
        persist: Callable[[StoredDraft], Awaitable[None]] | None = None,
    ) -> None:
        self.thread_id = thread_id
        self.draft_id = stored.draft_id if stored else f"draft-{uuid4().hex}"
        self.document = composer_document()
        self.options = options_document()
        if stored:
            self.document.apply_update(stored.document_update)
            self.options.apply_update(stored.options_update)
            composer_values(self.document)
            composer_options(self.options)
        self._options_update = self.options.get_update()
        self._options_update_base64 = base64.b64encode(self._options_update).decode("ascii")
        self._persist = persist
        self._update = self.document.get_update()
        self._update_base64 = base64.b64encode(self._update).decode("ascii")
        self.unsent_since: datetime | None = stored.unsent_since if stored else None
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

    def frame(self, participant: str, *, include_options: bool = True) -> DraftFrame:
        self.expire_presence()
        return DraftFrame(
            draft_id=self.draft_id,
            participant_id=participant,
            update_base64=self._update_base64,
            options_update_base64=self._options_update_base64 if include_options else None,
            participants=dict(self.participants),
            closed=self.closed,
        )

    def prepare_clear(self, capture: DraftCapture, *, reset_mode: bool = False) -> tuple[bytes, bytes | None]:
        """Validate a synchronized capture without changing the shared draft."""
        if self.closed or capture.draft_id != self.draft_id:
            raise HarnessUiError("The shared draft instance changed.", code="draft_instance_conflict")
        update = base64.b64decode(capture.update_base64, validate=True)
        if len(update) > MAX_DRAFT_BYTES:
            raise ValueError("Shared draft exceeds 512 KiB of CRDT state.")
        captured = composer_document()
        captured.apply_update(update)
        composer_values(captured)
        # A submission may consume an older acknowledged snapshot, but must not
        # introduce edits or deletions which the room has never accepted.
        merged = composer_document()
        merged.apply_update(self._update)
        before = merged.get_update()
        merged.apply_update(update)
        if merged.get_update() != before:
            raise HarnessUiError("Synchronize your edits before sending.", code="draft_not_synchronized")
        options_clear = None
        if capture.options_update_base64 is not None:
            selected = options_document()
            selected.apply_update(base64.b64decode(capture.options_update_base64, validate=True))
            composer_options(selected)
            combined = options_document()
            combined.apply_update(self._options_update)
            before_options = combined.get_update()
            combined.apply_update(selected.get_update())
            if combined.get_update() != before_options:
                raise HarnessUiError("Synchronize your options before sending.", code="draft_not_synchronized")
            if reset_mode and "mode" in selected.get("options", type=Map):
                del selected.get("options", type=Map)["mode"]
                options_clear = selected.get_update()
        del captured.get("text", type=Text)[:]
        return captured.get_update(), options_clear

    async def clear(self, updates: tuple[bytes, bytes | None]) -> bool:
        """Consume captured identities while preserving later and concurrent edits."""
        async with self._lock:
            if self.closed:
                return False
            candidate = composer_document()
            candidate.apply_update(self._update)
            candidate.apply_update(updates[0])
            options = options_document()
            options.apply_update(self._options_update)
            if updates[1] is not None:
                options.apply_update(updates[1])
            return await self._publish(candidate, options)

    async def _publish(self, candidate: Doc, options: Doc) -> bool:
        from anyio import CancelScope

        update = candidate.get_update()
        options_update = options.get_update()
        if len(update) + len(options_update) > MAX_DRAFT_BYTES:
            raise ValueError("Shared draft exceeds 512 KiB of CRDT state.")
        was_unsent = self.unsent_since is not None
        since = self.unsent_since
        # Live tokens count even during uploads; dormant entries and options do not.
        if not str(candidate.get("text", type=Text)).strip():
            since = None
        elif since is None:
            since = datetime.now(UTC)
        # Commit and publication are one editing acknowledgment. No peer observes
        # candidate state if saving fails, or an unsaved acknowledgment on cancellation.
        with CancelScope(shield=True):
            if self._persist is not None and (update != self._update or options_update != self._options_update):
                await self._persist(
                    StoredDraft(
                        thread_id=self.thread_id,
                        draft_id=self.draft_id,
                        document_update=update,
                        options_update=options_update,
                        unsent_since=since,
                        updated_at=datetime.now(UTC),
                    )
                )
            self.document = candidate
            self.options = options
            self._options_update = options_update
            self._options_update_base64 = base64.b64encode(options_update).decode("ascii")
            self._update = update
            self._update_base64 = base64.b64encode(update).decode("ascii")
            self.unsent_since = since
            self._notify()
        return was_unsent != (self.unsent_since is not None)

    async def command(
        self,
        participant: str,
        command: DraftCommand,
        validate_attachments: Callable[[tuple[str, ...]], Awaitable[None]],
        *,
        edit_options: bool = True,
    ) -> bool:
        """Apply an edit and report a change in nonempty draft membership."""
        async with self._lock:
            was_unsent = self.unsent_since is not None
            if self.closed or participant not in self.participants or command.draft_id != self.draft_id:
                raise HarnessUiError("The shared draft instance changed.", code="draft_instance_conflict")
            if command.kind == "presence":
                if (
                    command.presence is None
                    or command.update_base64 is not None
                    or command.options_update_base64 is not None
                ):
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
                candidate.apply_update(self._update)
                candidate.apply_update(update)
                _, attachments = composer_values(candidate)
                options = options_document()
                options.apply_update(self._options_update)
                if command.options_update_base64 is not None:
                    if not edit_options:
                        raise HarnessUiError("Sharing guests cannot change composer options.", code="share_forbidden")
                    options_update = base64.b64decode(command.options_update_base64, validate=True)
                    if len(options_update) > MAX_DRAFT_BYTES:
                        raise ValueError("Shared draft exceeds 512 KiB of CRDT state.")
                    options.apply_update(options_update)
                composer_options(options)
                accepted_update = candidate.get_update()
                if len(accepted_update) > MAX_DRAFT_BYTES:
                    raise ValueError("Shared draft exceeds 512 KiB of CRDT state.")
                await validate_attachments(attachments)
                # Publish only after the entire composer has validated. Rejected
                # selections cannot partially replace the shared text.
                return await self._publish(candidate, options)
            self._notify()
            return was_unsent != (self.unsent_since is not None)

    def close(self) -> None:
        self.closed = True
        self.participants.clear()
        self._presence_updated.clear()
        self._notify()
