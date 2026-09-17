"""One short persisted-Principal check before Control binds an Attempt use."""

from __future__ import annotations

from a13n_environment import EnvironmentAction, EnvironmentError
from a13n_environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY
from a13n_harness import EnvironmentAccess
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam.authorization import WorkspaceAction, authorize_persisted_agent_principal_actions
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, SessionRecord, ThreadRecord
from a13n_service.storage import short_session
from a13n_service.temporal import assume_utc, utc_now

from ..models import EnvironmentProviderRecord, EnvironmentRecord
from .authority import UseIdentity


class ClientUseAuthorization:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def __call__(self, use: UseIdentity) -> frozenset[EnvironmentAction]:
        # Selection, fence and target eligibility must share one database
        # observation, not six independently timed row reads.
        query = (
            select(RunRecord, EnvironmentRecord, RunAttemptRecord.lease_expires_at)
            .join(RunAttemptRecord, RunRecord.current_run_attempt_id == RunAttemptRecord.id)
            .join(ThreadRecord, RunRecord.thread_id == ThreadRecord.id)
            .join(SessionRecord, RunRecord.session_id == SessionRecord.id)
            .join(EnvironmentRecord, RunRecord.environment_id == EnvironmentRecord.id)
            .join(EnvironmentProviderRecord, EnvironmentRecord.provider_id == EnvironmentProviderRecord.id)
            .where(
                RunRecord.id == use.run_id,
                RunRecord.organization_id == use.connection.organization_id,
                RunRecord.status == "running",
                RunRecord.environment_access.is_not(None),
                ThreadRecord.current_run_id == RunRecord.id,
                RunAttemptRecord.id == use.attempt_id,
                RunAttemptRecord.attempt_number == use.attempt_fence,
                RunAttemptRecord.worker_id == use.worker_instance_id,
                RunAttemptRecord.status.in_(("leased", "running")),
                EnvironmentRecord.id == use.connection.environment_id,
                EnvironmentRecord.organization_id == use.connection.organization_id,
                EnvironmentRecord.workspace_id == SessionRecord.workspace_id,
                EnvironmentRecord.ownership == "external",
                EnvironmentRecord.status != "deleted",
                EnvironmentProviderRecord.type == WEBSOCKET_PROVIDER_KEY,
                EnvironmentProviderRecord.enabled.is_(True),
                EnvironmentProviderRecord.organization_id == use.connection.organization_id,
                or_(
                    EnvironmentProviderRecord.workspace_id.is_(None),
                    EnvironmentProviderRecord.workspace_id == SessionRecord.workspace_id,
                ),
            )
        )
        async with short_session(self._sessions) as session:
            selected = (await session.execute(query)).one_or_none()
            if selected is None:
                raise EnvironmentError(
                    "The Attempt has no current accepted Environment use", code="environment_forbidden"
                )
            run, environment, expires_at = selected
            await authorize_persisted_agent_principal_actions(
                session,
                principal=run.to_resource().authority_principal,
                organization_id=run.organization_id,
                workspace_id=environment.workspace_id,
                agent_id=run.agent_id,
                actions=frozenset({WorkspaceAction.environment_use, WorkspaceAction.agent_invoke}),
            )
            if assume_utc(expires_at) <= utc_now():
                raise EnvironmentError("The Attempt lease expired during use admission", code="environment_forbidden")
            return (
                EnvironmentAccess(run.environment_access).permission_set().operations
                & EnvironmentAccess(environment.access).permission_set().operations
            )
