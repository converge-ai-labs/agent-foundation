"""Reclaim deleted Threads' root checkpoints and unattached exclusive payloads."""

from a13n_logging import get_logger
from anyio import sleep
from sqlalchemy import select

from a13n_harness_ui.errors import ObjectIntegrityError

from .continuations import ContinuationPayload, ContinuationState
from .contracts import ReferencedContinuation
from .database import DatabaseSessions, short_session
from .models import PlannedRestartRecord, ThreadRecord
from .objects import ImmutableObjectStore, ObjectKind, ObjectRef

_LOGGER = get_logger(__name__)
_PAYLOAD_KINDS = {ObjectKind.continuation_state, ObjectKind.continuation_display}


class CheckpointPruner:
    def __init__(self, sessions: DatabaseSessions, objects: ImmutableObjectStore) -> None:
        self._sessions = sessions
        self._objects = objects

    async def prune(self, *, limit: int = 100) -> int:
        """Optimistically scan, then delete bounded batches only if use stayed idle."""
        from a13n_harness_ui.restart_models import RestartBatch

        if limit <= 0:
            return 0
        access = self._objects.checkpoint_access
        generation = await access.snapshot()
        if generation is None:
            return 0
        # Detached metadata; never keep a DB transaction across file I/O.
        async with short_session(self._sessions) as session:
            rows = (
                await session.execute(
                    select(ThreadRecord.thread_id, ThreadRecord.continuation_digest, ThreadRecord.completion_digest)
                )
            ).all()
            restart = await session.get(PlannedRestartRecord, 1)
            batch = None if restart is None else RestartBatch.model_validate_json(restart.payload)
        owners = {row.thread_id for row in rows}
        retained = {digest for row in rows for digest in (row.continuation_digest, row.completion_digest) if digest}
        if batch is not None:
            retained.update(item.checkpoint.logical_digest for item in batch.items)
        references = await self._objects.references()
        attached: set[ObjectRef] = set()
        candidates: list[tuple[ObjectRef, ...]] = []
        for reference in references:
            if reference.object_kind != ObjectKind.continuation:
                continue
            # Abort, rather than guessing ownership/reachability from a damaged
            # or unsupported manifest. The App isolates this maintenance error.
            stored = (await self._objects.read_model(reference, ContinuationPayload)).root
            if isinstance(stored, ReferencedContinuation):
                payloads = (stored.native_state,) + (() if stored.display is None else (stored.display,))
                attached.update(payloads)
                native = await self._objects.read_model(stored.native_state, ContinuationState)
                if native.checkpoint_id != stored.checkpoint_id:
                    raise ObjectIntegrityError("Checkpoint owner mismatch", code="object_payload_incompatible")
                owner = native.harness_state.thread_id
            else:
                payloads = ()
                owner = stored.harness_state.thread_id
            # Existing Threads retain all historical final checkpoints, not just
            # the latest SQL heads. Archive/clear does not change that policy.
            if owner not in owners and reference.logical_digest not in retained:
                candidates.append((reference, *payloads))
            if await access.snapshot() != generation:
                return 0
            await sleep(0)
        candidates.extend((ref,) for ref in references if ref.object_kind in _PAYLOAD_KINDS and ref not in attached)
        removed = 0
        # Release the registration gate between small batches so foreground
        # work can enter. Any new use invalidates the remaining scan.
        for offset in range(0, min(len(candidates), limit), 10):
            async with access.unchanged(generation) as unchanged:
                if not unchanged:
                    break
                for group in candidates[offset : min(offset + 10, limit)]:
                    try:
                        for reference in group:
                            # Manifest first: a failed unlink leaves its payloads
                            # protected; partial payload removal retries next pass.
                            await self._objects.remove(reference)
                            removed += 1
                    except OSError as exc:
                        _LOGGER.warning("Could not prune checkpoint", extra={"error_type": type(exc).__name__})
            await sleep(0)
        return removed
