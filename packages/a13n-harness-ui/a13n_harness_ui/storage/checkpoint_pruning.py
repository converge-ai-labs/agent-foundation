"""Best-effort checkpoint cleanup for explicitly deleted Threads only."""

from a13n_logging import get_logger
from anyio import sleep
from sqlalchemy import select

from a13n_harness_ui.errors import ObjectIntegrityError

from .continuations import ContinuationPayload, ContinuationState
from .contracts import ReferencedContinuation
from .database import DatabaseSessions, short_session
from .models import DeletedThreadRecord, PlannedRestartRecord, ThreadRecord
from .objects import ImmutableObjectStore, ObjectKind

_LOGGER = get_logger(__name__)


class CheckpointPruner:
    def __init__(self, sessions: DatabaseSessions, objects: ImmutableObjectStore) -> None:
        self._sessions = sessions
        self._objects = objects

    async def prune(self, *, limit: int = 100) -> int:
        """Remove up to limit checkpoints owned by recorded permanent deletions."""
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
        removed = attempted = 0
        for reference in await self._objects.references():
            if reference.object_kind != ObjectKind.continuation or reference.logical_digest in retained:
                continue
            try:
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
                attempted += 1
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
            if attempted >= limit:
                break
            await sleep(0)
        return removed
