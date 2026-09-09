"""Distribution-owned built-in Agent registration."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    authorize_workspace,
)
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.resource_keys import insert_with_key
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .domain import (
    AgentRevisionCreateResult,
    AgentSource,
    BuiltinAgentRegistration,
    new_agent_revision_id,
)
from .errors import (
    AgentError,
    builtin_identity_conflict,
    map_authorization_error,
)
from .models import AgentRecord, AgentRevisionRecord
from .persistence import (
    lock_revision,
    new_agent_audit,
    new_builtin_agent,
    new_revision,
)
from .resolution import AgentResolver, resolution_error


class BuiltinAgents:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        resolver: AgentResolver,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._resolver = resolver
        self._clock = clock

    async def register_builtin(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        registration: BuiltinAgentRegistration,
    ) -> AgentRevisionCreateResult:
        """Register or upgrade one distribution-owned built-in Agent."""

        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.agent_create,
                )
                current = await session.get(AgentRecord, registration.agent_id)
                if current is not None and (
                    current.organization_id != workspace.organization_id
                    or current.workspace_id != workspace.workspace_id
                    or current.source != AgentSource.builtin.value
                ):
                    raise builtin_identity_conflict()
                organization_id = workspace.organization_id
        except AuthorizationError as error:
            raise map_authorization_error(error) from error

        try:
            prepared = await self._resolver.prepare(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_id=registration.agent_id,
                config=registration.config,
            )
        except Exception as error:
            raise resolution_error(error) from error

        expected_content_digest: str | None = None
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.agent_create,
                )
                record = await session.scalar(
                    select(AgentRecord).where(AgentRecord.id == registration.agent_id).with_for_update()
                )
                created = record is None
                revision_id = new_agent_revision_id()
                if created:
                    record = new_builtin_agent(
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        revision_id=revision_id,
                        registration=registration,
                        now=now,
                    )
                elif (
                    record.organization_id != workspace.organization_id
                    or record.workspace_id != workspace.workspace_id
                    or record.source != AgentSource.builtin.value
                ):
                    raise builtin_identity_conflict()
                elif not record.enabled or record.archived_at is not None:
                    raise AgentError(
                        "agent_state_conflict",
                        "The built-in Agent is not available.",
                        category=ErrorCategory.conflict,
                    )

                try:
                    resolved = await self._resolver.freeze_in_transaction(session, prepared=prepared)
                except Exception as error:
                    raise resolution_error(error) from error
                if created:
                    await insert_with_key(session, record, prefix="agent")
                revision = new_revision(
                    record,
                    revision_id=revision_id,
                    version=1 if created else record.version + 1,
                    config=registration.config,
                    resolved=resolved,
                    source_revision_id=None,
                    actor=actor,
                    now=now,
                )
                revision.created_by_type = "system"
                revision.created_by_id = registration.system_actor_id
                expected_content_digest = revision.content_digest
                current_revision = (
                    None
                    if created
                    else await lock_revision(
                        session,
                        organization_id=record.organization_id,
                        workspace_id=record.workspace_id,
                        agent_id=record.id,
                        revision_id=record.current_revision_id,
                    )
                )
                content_changed = current_revision is None or current_revision.content_digest != revision.content_digest
                metadata_changed = record.name != registration.name or record.description != registration.description
                if not content_changed and not metadata_changed:
                    assert current_revision is not None
                    return AgentRevisionCreateResult(
                        agent=record.to_resource(),
                        revision=current_revision.to_resource(),
                    )

                record.name = registration.name
                record.description = registration.description
                record.updated_by_type = "system"
                record.updated_by_id = registration.system_actor_id
                record.updated_at = now
                if content_changed:
                    record.version = revision.version
                    record.current_revision_id = revision.id
                    session.add(revision)
                session.add(
                    new_agent_audit(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        action="agent.builtin.register",
                        agent_id=record.id,
                        now=now,
                    )
                )
                await session.flush()
                selected_revision = revision if content_changed else current_revision
                assert selected_revision is not None
                return AgentRevisionCreateResult(
                    agent=record.to_resource(),
                    revision=selected_revision.to_resource(),
                )
        except AuthorizationError as error:
            raise map_authorization_error(error) from error
        except IntegrityError:
            if expected_content_digest is not None:
                try:
                    replay = await self._builtin_registration_replay(
                        actor=actor,
                        workspace_id=workspace_id,
                        registration=registration,
                        expected_content_digest=expected_content_digest,
                    )
                except AuthorizationError as authorization_error:
                    raise map_authorization_error(authorization_error) from authorization_error
                if replay is not None:
                    return replay
            raise

    async def _builtin_registration_replay(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        registration: BuiltinAgentRegistration,
        expected_content_digest: str,
    ) -> AgentRevisionCreateResult | None:
        async with transaction(self._sessions) as session:
            workspace = await authorize_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.agent_create,
            )
            record = await session.scalar(
                select(AgentRecord).where(
                    AgentRecord.id == registration.agent_id,
                    AgentRecord.organization_id == workspace.organization_id,
                    AgentRecord.workspace_id == workspace.workspace_id,
                    AgentRecord.source == AgentSource.builtin.value,
                )
            )
            if (
                record is None
                or record.name != registration.name
                or record.description != registration.description
                or not record.enabled
                or record.archived_at is not None
            ):
                return None
            revision = await session.scalar(
                select(AgentRevisionRecord).where(
                    AgentRevisionRecord.id == record.current_revision_id,
                    AgentRevisionRecord.agent_id == record.id,
                    AgentRevisionRecord.content_digest == expected_content_digest,
                )
            )
            if revision is None:
                return None
            return AgentRevisionCreateResult(agent=record.to_resource(), revision=revision.to_resource())
