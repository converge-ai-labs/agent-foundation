"""Offline, restartable conversion of pre-pagination run objects after the schema upgrade.

The old display column was renamed to tail without converting its JSON pointer or its plain JSON object.
Its checkpoint is plain JSON too. Back up both verified originals before writing new compressed objects;
move both pointers together only after rechecking the sealed row. Never delete the old objects.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import zstandard
from a13n_logging import get_logger
from anyio.to_thread import run_sync
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import ColumnElement, exists, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.objects.interface import ObjectRef, ObjectStore, new_key, read
from a13n_service.infra.outbox import OutboxRow
from a13n_service.runs.checkpoints import FORMAT, Pointer, RunState, StatePointer, TailPointer, run_prefix
from a13n_service.runs.display import Item, StreamPosition, Tail
from a13n_service.runs.tables import RunItemPageRow, RunRow

logger = get_logger(__name__)


class LegacyPointer(Pointer):
    position: StreamPosition


class LegacyDisplay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, JsonValue]] = Field(default_factory=list)
    position: StreamPosition
    resume_after: str | None = None
    dropped: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class Source:
    id: str
    organization_id: str
    version: int
    status: str
    tail: dict
    checkpoint: dict | None


def legacy_objects() -> ColumnElement[bool]:
    return RunRow.tail.is_not(None) & or_(
        RunRow.tail["first"].as_integer().is_(None), RunRow.tail["count"].as_integer().is_(None)
    )


async def require_current(storage: Storage) -> None:
    """Startup and deployment checks must not report success with unreadable historical runs."""
    async with short_session(storage) as session:
        legacy = await session.scalar(select(exists().where(legacy_objects())))
    if legacy:
        raise RuntimeError(
            "Legacy run objects require conversion; stop all Service replicas and run "
            "a13n-service migrate-run-objects --apply --backup-dir PATH before starting this build"
        )


def convert_display(raw: bytes, pointer: LegacyPointer) -> Tail:
    """Keep the retained items and their order; refuse already-truncated history rather than invent it."""
    display = LegacyDisplay.model_validate_json(raw)
    if pointer.format != FORMAT or display.position != pointer.position:
        raise ValueError("Legacy display format or stream position does not match its pointer")
    if display.dropped:
        raise ValueError("Legacy display has dropped items; it needs a separate recovery plan")
    if any("ordinal" in item or "content_refs" in item for item in display.items):
        raise ValueError("Legacy display contains fields from a newer format")
    items = [Item.model_validate({**item, "ordinal": index}) for index, item in enumerate(display.items, 1)]
    if len({item.id for item in items}) != len(items):
        raise ValueError("Legacy display repeats an item ID")
    return Tail(items=items, position=display.position, resume_after=display.resume_after)


async def _original(objects: ObjectStore, pointer: Pointer) -> bytes:
    return await read(objects, ObjectRef(pointer.key, pointer.digest, pointer.size, "application/json"))


def _json_bytes(data: bytes) -> bytes:
    # Pre-pagination objects are JSON. Accept zstd too when an operator already converted just the encoding.
    return zstandard.ZstdDecompressor().decompress(data) if data.startswith(b"\x28\xb5\x2f\xfd") else data


def _backup(directory: Path, source: Source, display: bytes, state: bytes | None) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix=f"{source.id}-", dir=directory))
    files = {"row.json": json.dumps(asdict(source), indent=2).encode(), "display.bin": display}
    if state is not None:
        files["checkpoint.bin"] = state
    for name, data in files.items():
        with os.fdopen(os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())


async def _publish(objects: ObjectStore, source: Source, kind: str, attempt: int, data: bytes) -> ObjectRef:
    key = new_key(f"{run_prefix(source.organization_id, source.id)}/{kind}/{attempt}")
    compressed = zstandard.ZstdCompressor().compress(data)
    ref = await objects.put(key, compressed, content_type="application/zstd")
    # Do not move a durable pointer on an acknowledgement alone.
    if await read(objects, ref) != compressed:
        raise ValueError("Converted run object did not read back unchanged")
    return ref


async def _quiescent(storage: Storage) -> None:
    async with short_session(storage) as session:
        if await session.scalar(select(exists().where(RunRow.status.in_(("accepted", "running"))))):
            raise RuntimeError("Run object conversion requires no accepted or running runs; drain Service first")


async def _check_cleanup(session: AsyncSession, run_id: str) -> None:
    # A durable old cleanup intent could delete the replacements after replicas resume. Do not rewrite it.
    pending = await session.scalar(
        select(
            exists().where(
                OutboxRow.kind == "checkpoint_cleanup",
                OutboxRow.target["run_id"].as_string() == run_id,
                OutboxRow.status != "delivered",
            )
        )
    )
    if pending:
        raise RuntimeError(f"Run {run_id} has unfinished checkpoint cleanup; settle it before conversion")
    if await session.scalar(select(exists().where(RunItemPageRow.run_id == run_id))):
        raise RuntimeError(f"Run {run_id} already has display pages; refusing a mixed-format conversion")


async def _commit(storage: Storage, source: Source, tail: dict, checkpoint: dict | None) -> None:
    async with transaction(storage) as session:
        await session.execute(text("SET LOCAL lock_timeout = '3s'"))
        # Flush constraint triggers in this statement, so PostgreSQL can re-enable the guard before commit.
        await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        # This maintenance operation changes representation, not sealed facts. The table lock prevents another
        # writer from using the temporarily disabled guard; rollback also restores the trigger on any failure.
        await session.execute(text("LOCK TABLE runs IN SHARE ROW EXCLUSIVE MODE"))
        enabled = await session.scalar(
            text("SELECT tgenabled FROM pg_trigger WHERE tgrelid = 'runs'::regclass AND tgname = 'guard_run'")
        )
        if enabled != "O":
            raise RuntimeError("Run immutability guard is not enabled")
        await _check_cleanup(session, source.id)
        current = await session.get(RunRow, source.id)
        if (
            current is None
            or current.version != source.version
            or current.status != source.status
            or current.tail != source.tail
            or current.checkpoint != source.checkpoint
        ):
            raise RuntimeError(f"Run {source.id} changed during conversion; no pointers were replaced")
        await session.execute(text("ALTER TABLE runs DISABLE TRIGGER guard_run"))
        await session.execute(update(RunRow).where(RunRow.id == source.id).values(tail=tail, checkpoint=checkpoint))
        await session.execute(text("ALTER TABLE runs ENABLE TRIGGER guard_run"))


async def migrate(
    storage: Storage, objects: ObjectStore, *, backup_dir: Path | None = None, apply: bool = False, limit: int = 100
) -> int:
    """Inspect at most `limit` legacy runs, or convert them offline with one backed-up commit per run.

    Run after schema migration and with all Service replicas stopped. A successful row is absent on retry;
    failure leaves that row's old pointers and objects usable by the old build and its originals backed up.
    """
    if apply and backup_dir is None:
        raise ValueError("Applying run object conversion requires a backup directory")
    if not 1 <= limit <= 1000:
        raise ValueError("Run object conversion limit must be between 1 and 1000")
    await _quiescent(storage)
    async with short_session(storage) as session:
        rows = (
            await session.execute(
                select(RunRow.id, RunRow.organization_id, RunRow.version, RunRow.status, RunRow.tail, RunRow.checkpoint)
                .where(legacy_objects())
                .order_by(RunRow.id)
                .limit(limit)
            )
        ).all()
        sources = [Source(*row) for row in rows]
        for source in sources:
            await _check_cleanup(session, source.id)
    for source in sources:
        if source.status not in ("waiting", "completed", "failed", "cancelled"):
            raise RuntimeError(f"Run {source.id} is not sealed")
        pointer = LegacyPointer.model_validate(source.tail)
        original = await _original(objects, pointer)
        tail = convert_display(_json_bytes(original), pointer)
        state_pointer = StatePointer.model_validate(source.checkpoint) if source.checkpoint is not None else None
        state = await _original(objects, state_pointer) if state_pointer is not None else None
        if state is not None:
            parsed = RunState.model_validate_json(_json_bytes(state))
            assert state_pointer is not None
            if (
                state_pointer.format != FORMAT
                or parsed.seq != state_pointer.seq
                or parsed.attempt != state_pointer.attempt
                or state_pointer.refs
                or parsed.harness.refs
            ):
                raise ValueError("Legacy checkpoint format, sequence, attempt or dependencies are incompatible")
        if apply:
            assert backup_dir is not None
            await run_sync(_backup, backup_dir, source, original, state)
            ref = await _publish(objects, source, "tail", pointer.position.attempt, tail.model_dump_json().encode())
            converted_tail = TailPointer(
                key=ref.key,
                digest=ref.digest,
                size=ref.size,
                format=FORMAT,
                position=tail.position,
                first=1,
                count=len(tail.items),
            ).model_dump(mode="json")
            converted_state = source.checkpoint
            if state is not None:
                assert state_pointer is not None
                ref = await _publish(objects, source, "state", state_pointer.attempt, _json_bytes(state))
                converted_state = state_pointer.model_copy(
                    update={"key": ref.key, "digest": ref.digest, "size": ref.size}
                ).model_dump(mode="json")
            await _commit(storage, source, converted_tail, converted_state)
        logger.info(
            "Legacy run objects converted" if apply else "Legacy run objects verified",
            extra={"run_id": source.id, "item_count": len(tail.items)},
        )
    return len(sources)
