"""How a file a message carries, an asset or a URL's content, reaches the model.

The model reads a file as message content when it can: natively when its model declares it understands the type,
which covers the image, audio and video types Pydantic AI maps to a provider format and PDF, and text of at most
`INLINE_TEXT_BYTES` inline, decoded with its declared charset. Any other file is placed in the run's primary
environment at `/workspace/.a13n/attachments/{SHA-256 of its bytes}/{name}`, and the model reads a reference to
it; the environment's file tools read it from there. A file already at that path with the attachment's size is
left as it is, so offering an entry again writes nothing twice; one of another size is replaced. A run that can
do neither refuses the file: an asset when its message is submitted, edited or accepted, and a URL's content
when the worker fetches it, except text, which is inlined truncated.

Bytes are read only when the model or the environment needs them, one file at a time, and not kept afterwards.
"""

import asyncio
import hashlib
import posixpath
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, get_args

from a13n_environment.models import EnvironmentError
from a13n_harness import ModelCapability
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.media_types import is_text_media_type
from pydantic_ai.messages import (
    AudioMediaType,
    BinaryContent,
    ImageMediaType,
    TextContent,
    UserContent,
    VideoMediaType,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import ServiceError
from a13n_service.resources.agents.schemas import AgentOverride, apply_override
from a13n_service.resources.agents.service import SelectedRevision
from a13n_service.resources.assets.tables import AssetRow
from a13n_service.resources.models.service import resolve_model
from a13n_service.runs import placement
from a13n_service.runs.schemas import AssetPart, MessagePayload
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope

# The longest text, in bytes, the model reads inline.
INLINE_TEXT_BYTES = 64 * 1024
# Placed file names stay well inside the 255-byte limit of common filesystems.
_NAME_BYTES = 200
_ROOT = f"{placement.ROOT}/attachments"
_NATIVE: Mapping[str, ModelCapability] = {
    **dict.fromkeys(get_args(ImageMediaType), ModelCapability.IMAGE_UNDERSTANDING),
    **dict.fromkeys(get_args(AudioMediaType), ModelCapability.AUDIO_UNDERSTANDING),
    **dict.fromkeys(get_args(VideoMediaType), ModelCapability.VIDEO_UNDERSTANDING),
    # The one document type every provider that reads documents takes.
    "application/pdf": ModelCapability.DOCUMENT_UNDERSTANDING,
}
# Environment answers a later attempt would get again: the file cannot be placed where its name puts it.
_REFUSED = frozenset(
    {
        "environment_request_invalid",
        "environment_denied",
        "environment_conflict",
        "environment_too_large",
        "environment_unsupported",
    }
)


@dataclass(frozen=True, slots=True)
class Recipient:
    """What a run takes a file as: the content its model understands, and whether it mounts a primary environment."""

    capabilities: frozenset[ModelCapability]
    primary: bool

    def receives(
        self, media_type: str, size: int, *, field: str, truncatable: bool = False
    ) -> Literal["native", "text", "file"]:
        """How the model reads a file of `media_type` and `size` bytes; `invalid_argument` at `field` if it cannot.

        A `truncatable` text too long to inline is inlined truncated when there is no environment to place it in.
        """
        if _NATIVE.get(media_type) in self.capabilities:
            return "native"
        if is_text_media_type(media_type) and (size <= INLINE_TEXT_BYTES or (truncatable and not self.primary)):
            return "text"
        if self.primary:
            return "file"
        raise ServiceError(
            "invalid_argument",
            "The model cannot read this file and the run has no environment to place it in",
            {"field": field, "reason": "environment_required", "media_type": media_type},
        )


@dataclass(frozen=True, slots=True)
class Attached:
    """A file a message names, and how to read its bytes."""

    name: str
    media_type: str
    size: int
    field: str
    source_id: str
    read: Callable[[], Awaitable[bytes]]
    # The SHA-256 of its bytes, when known without reading them.
    digest: str | None = None
    charset: str = "utf-8"
    identifier: str | None = None
    # A URL's content is known only once fetched, so its text is truncated rather than refused.
    truncatable: bool = False


async def attach(recipient: Recipient, environment: BoundEnvironment, file: Attached) -> UserContent:
    """The content the model reads for `file`, placed in the primary environment first when it reads a reference.

    Inline text and references are hidden from viewers, who see the attachment itself.
    """
    received = recipient.receives(file.media_type, file.size, field=file.field, truncatable=file.truncatable)
    if received == "native":
        return BinaryContent(await file.read(), media_type=file.media_type, identifier=file.identifier)
    name = _segment(file.name)
    label = f'Attachment "{name}" ({file.media_type}, {file.size} bytes)'
    metadata = {"source_id": file.source_id, "display": False}
    if received == "text":
        data = await file.read()
        if len(data) > INLINE_TEXT_BYTES:
            label += f", truncated to its first {INLINE_TEXT_BYTES} bytes"
        text = data[:INLINE_TEXT_BYTES].decode(file.charset, errors="replace")
        return TextContent(f"{label}:\n{text}", metadata=metadata)
    path = await _place(environment, file, name)
    return TextContent(f"{label}, placed in the environment at: {path}", metadata=metadata)


def _segment(name: str) -> str:
    """`name` as one path segment: without control characters or `/`, at most `_NAME_BYTES` UTF-8 bytes long with
    its extension kept, or `attachment` when nothing usable is left."""
    name = "".join("_" if char == "/" else char for char in name if char >= " " and char != "\x7f")
    stem, extension = posixpath.splitext(name)
    # Lone surrogates have no UTF-8 form.
    stem_bytes, extension_bytes = stem.encode(errors="ignore"), extension.encode(errors="ignore")
    if len(extension_bytes) > _NAME_BYTES // 2:
        stem_bytes, extension_bytes = stem_bytes + extension_bytes, b""
    # A character cut at the limit is dropped.
    segment = (stem_bytes[: _NAME_BYTES - len(extension_bytes)] + extension_bytes).decode(errors="ignore")
    return "attachment" if segment in {"", ".", ".."} else segment


async def _place(environment: BoundEnvironment, file: Attached, name: str) -> str:
    """Write `file` unless the primary environment has it with its size already; returns its path.

    A write the environment refuses is `invalid_argument`, since another attempt would be refused too; any other
    failure is `unavailable`, and a later attempt places the file again.
    """
    digest = file.digest or await asyncio.to_thread(_sha256, await file.read())
    path = f"{_ROOT}/{digest}/{name}"
    try:
        route = await environment.resolve_files(path)
        async with environment.open_files(route) as files:
            placed = await placement.stat(files, path)
            if placed is None or placed.size != file.size:
                await files.mkdir(posixpath.dirname(path), parents=True, exist_ok=True)
                await placement.write(files, path, await file.read())
    except EnvironmentError as error:
        if error.code in _REFUSED or error.retry_hint == "request_change":
            raise ServiceError(
                "invalid_argument",
                "The environment refused to place this file",
                {"field": file.field, "reason": "placement_refused", "code": error.code},
            ) from None
        raise ServiceError(
            "unavailable",
            "An attachment could not be placed in the environment",
            {"dependency": "environment", "reason": error.code},
        ) from None
    return path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def asset_fields(payload: MessagePayload) -> dict[str, str]:
    """The asset each asset part of `payload` names, by the part's field."""
    return {
        f"content.{index}.asset_id": part.asset_id
        for index, part in enumerate(payload.content)
        if isinstance(part, AssetPart)
    }


async def require_readable(
    session: AsyncSession,
    principal: Principal,
    scope: WorkspaceScope,
    revision: SelectedRevision,
    overrides: AgentOverride | None,
    payload: MessagePayload,
    *,
    authority: ExecutionAuthority,
    primary: bool,
) -> None:
    """Refuse an asset of `payload` that a run of `revision` with `overrides` could not read: its model reads it
    neither natively nor inline and, without `primary`, no environment takes it."""
    fields = asset_fields(payload)
    if not fields:
        return
    config = revision.config if overrides is None else apply_override(revision.config, overrides)
    model = await resolve_model(session, principal, scope, config.model, authority=authority)
    recipient = Recipient(model.config.characteristics.capabilities, primary)
    rows = await session.execute(
        select(AssetRow.id, AssetRow.content_type, AssetRow.size).where(
            AssetRow.workspace_id == scope.workspace_id, AssetRow.id.in_(set(fields.values()))
        )
    )
    assets = {row.id: row for row in rows}
    for field, asset_id in fields.items():
        # Assets are never deleted, and submission checked that each is the workspace's.
        asset = assets[asset_id]
        recipient.receives(asset.content_type, asset.size, field=field)
