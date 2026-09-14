"""Prepare completion-time queue handoff without holding execution authority locks."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam import AuthenticatedActor
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, utc_now

from .attempts import AttemptContext, read_attempt_authority
from .control_models import QueuedSubmissionRecord
from .domain import RunAttemptStatus
from .models import SessionRecord
from .objects import StoredRunState
from .queue_commands import QueuedRunCommands
from .queue_handoff import CompletionQueueHandoffService, QueueHandoffCommit
from .queue_validity import permanent_queue_failure


class QueueCompletion:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        commands: QueuedRunCommands,
        handoffs: CompletionQueueHandoffService,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._commands = commands
        self._handoffs = handoffs
        self._clock = clock

    async def prepare(self, authority: AttemptContext, state: StoredRunState) -> QueueHandoffCommit | None:
        async with short_session(self._sessions) as database:
            source, attempt, thread_record = await read_attempt_authority(database, authority, self._clock())
            # Unentered outcome recovery seals normally; terminal queue scanning follows.
            if attempt.status != RunAttemptStatus.running.value:
                return None
            first = await database.scalar(
                select(QueuedSubmissionRecord).where(
                    QueuedSubmissionRecord.organization_id == source.organization_id,
                    QueuedSubmissionRecord.thread_id == source.thread_id,
                    QueuedSubmissionRecord.position == 1,
                )
            )
            if first is None:
                return None
            owner = await database.get(SessionRecord, source.session_id)
            if owner is None:
                raise ValueError("Run Session is unavailable")
            workspace_id = owner.workspace_id
            run, thread, queued = source.to_resource(), thread_record.to_resource(), first.to_resource()
            failure = await permanent_queue_failure(
                database, organization_id=run.organization_id, workspace_id=workspace_id, queued=queued
            )

        if failure is not None:

            async def revalidate(database: AsyncSession) -> bool:
                return failure == await permanent_queue_failure(
                    database, organization_id=run.organization_id, workspace_id=workspace_id, queued=queued
                )

            return await self._handoffs.prepare_failure(
                authority=authority,
                source_state=state,
                queued_submission_id=queued.queued_submission_id,
                submission_digest_sha256=queued.submission_digest_sha256,
                failure=failure,
                expected_thread_version=thread.version,
                expected_queue_version=thread.queue_version,
                expected_head_run_id=thread.head_run_id,
                revalidate=revalidate,
            )
        prepared = await self._commands.prepare_queued_run(
            actor=AuthenticatedActor(
                principal=queued.authority_principal,
                auth_method="stored_queued_submission",
                credential_id=queued.queued_submission_id,
                boundary_workspace_id=workspace_id,
                request_id=queued.queued_submission_id,
            ),
            current=run,
            head=run,
            head_state=state.envelope,
            thread=thread,
            queued=queued,
            request_fingerprint=queued.submission_digest_sha256,
        )
        return await self._handoffs.prepare_consumption(
            authority=authority,
            source_state=state,
            successor_run=prepared.run,
            successor_state=prepared.state,
            queued_submission_id=queued.queued_submission_id,
            submission_digest_sha256=queued.submission_digest_sha256,
            accepted_input=prepared.input,
            expected_thread_version=thread.version,
            expected_queue_version=thread.queue_version,
            expected_head_run_id=thread.head_run_id,
            final_validator=prepared.validate,
        )
