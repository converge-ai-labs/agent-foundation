"""Best-effort checkpoint cleanup for explicitly deleted Threads only."""

from typing import Literal

from a13n_logging import get_logger
from anyio import sleep
from pydantic import BaseModel
from sqlalchemy import select

from a13n_harness_ui.errors import ObjectIntegrityError

from .continuations import ContinuationPayload, ContinuationState
from .contracts import ReferencedContinuation
from .database import DatabaseSessions, short_session
from .models import DeletedThreadRecord, PlannedRestartRecord, ThreadRecord
from .objects import ImmutableObjectStore, ObjectKind, ObjectRef

_LOGGER = get_logger(__name__)


class _OwnerState(BaseModel):
    thread_id: str


class _CheckpointFields(BaseModel):
    schema_version: Literal["1", "2"]
    harness_state: _OwnerState | None = None
    native_state: ObjectRef | None = None


class _CheckpointEnvelope(BaseModel):
    payload: _CheckpointFields


class CheckpointPruner:
    def __init__(self, sessions: DatabaseSessions, objects: ImmutableObjectStore) -> None:
        self._sessions = sessions
        self._objects = objects
        self._cursor: str | None = None

    async def _may_be_deleted(self, reference: ObjectRef, deleted: set[str]) -> bool:
        # Ignore history and schema-incompatible deferred input while filtering.
        # A match only admits the existing fully verified deletion path below.
        fields = (await self._objects.read_unverified_projection(reference, _CheckpointEnvelope)).payload
        if fields.schema_version == "2":
            native = fields.native_state
            if native is None or native.object_kind != ObjectKind.continuation_state:
                return False
            fields = (await self._objects.read_unverified_projection(native, _CheckpointEnvelope)).payload
        return fields.harness_state is not None and fields.harness_state.thread_id in deleted

    async def prune(self, *, limit: int = 100) -> int:
        """Inspect up to limit unretained checkpoints, resuming the scan next pass."""
        from a13n_harness_ui.restart_models import RestartBatch

        if limit <= 0:
            return 0
        async with short_session(self._sessions) as session:
            deleted = set(await session.scalars(select(DeletedThreadRecord.thread_id)))
            if not deleted:
                return 0
            rows = (
                await session.execute(select(ThreadRecord.continuation_digest, ThreadRecord.completion_digest))
            ).all()
            retained = {digest for row in rows for digest in row if digest}
            restart = await session.get(PlannedRestartRecord, 1)
            if restart is not None:
                retained.update(
                    item.checkpoint.logical_digest for item in RestartBatch.model_validate_json(restart.payload).items
                )
        # No transaction or global lock spans object I/O. New/forked Threads
        # never enter the deletion set, even before their SQL row is published.
        references = sorted(
            (
                ref
                for ref in await self._objects.references()
                if ref.object_kind == ObjectKind.continuation and ref.logical_digest not in retained
            ),
            key=lambda ref: ref.logical_digest,
        )
        remaining = [ref for ref in references if self._cursor is None or ref.logical_digest > self._cursor]
        if not remaining:
            remaining = references
        removed = 0
        for reference in remaining[:limit]:
            self._cursor = reference.logical_digest
            await sleep(0)
            try:
                if not await self._may_be_deleted(reference, deleted):
                    continue
                stored = (await self._objects.read_model(reference, ContinuationPayload)).root
                payloads = ()
                if isinstance(stored, ReferencedContinuation):
                    native = await self._objects.read_model(stored.native_state, ContinuationState)
                    if native.checkpoint_id != stored.checkpoint_id:
                        raise ObjectIntegrityError("Checkpoint owner mismatch", code="object_payload_incompatible")
                    owner = native.harness_state.thread_id
                    payloads = (stored.native_state,) + (() if stored.display is None else (stored.display,))
                else:
                    owner = stored.harness_state.thread_id
                if owner not in deleted:
                    continue
                # Manifest first. If it cannot be removed, leave its payloads
                # intact for a later pass. Unattached partials are out of scope.
                await self._objects.remove(reference)
                removed += 1
                for payload in payloads:
                    try:
                        await self._objects.remove(payload)
                        removed += 1
                    except OSError as exc:
                        _LOGGER.warning("Could not prune checkpoint payload", extra={"error_type": type(exc).__name__})
            except (OSError, ObjectIntegrityError) as exc:
                # A concurrent collector/replacement may have removed a file.
                # Unknown ownership never authorizes deletion of other objects.
                _LOGGER.warning("Could not prune checkpoint", extra={"error_type": type(exc).__name__})
        if len(remaining) <= limit:
            self._cursor = None
        return removed
