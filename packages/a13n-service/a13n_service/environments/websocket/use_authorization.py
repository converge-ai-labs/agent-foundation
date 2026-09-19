"""One short persisted-Principal check before Control binds an Attempt use."""

from __future__ import annotations

from a13n_environment import EnvironmentError
from a13n_environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY
from sqlalchemy import literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam.authorization import WorkspaceAction, authorize_persisted_agent_principal_actions
from a13n_service.iam.domain import AuthorizationError
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, SessionRecord, ThreadRecord
from a13n_service.storage import is_database_unavailable, short_session
from a13n_service.temporal import assume_utc, utc_now

from ..models import EnvironmentProviderRecord, EnvironmentRecord
from ..mount_models import RunEnvironmentMountRecord
from .authority import UseIdentity


class ClientUseAuthorization:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def __call__(self, use: UseIdentity) -> str:
        # Selection, fence and target eligibility must share one database
        # observation, not six independently timed row reads.
        bindings = union_all(
            select(
                RunRecord.id.label("run_id"),
                literal("workspace").label("name"),
                RunRecord.environment_id.label("environment_id"),
                RunRecord.environment_working_directory.label("working_directory"),
            ).where(RunRecord.environment_id.is_not(None)),
            select(
                RunEnvironmentMountRecord.run_id,
                RunEnvironmentMountRecord.name,
                RunEnvironmentMountRecord.environment_id,
                RunEnvironmentMountRecord.working_directory,
            ),
        ).subquery()
        query = (
            select(
                RunRecord,
                EnvironmentRecord,
                RunAttemptRecord.lease_expires_at,
                bindings.c.working_directory,
            )
            .join(RunAttemptRecord, RunRecord.current_run_attempt_id == RunAttemptRecord.id)
            .join(ThreadRecord, RunRecord.thread_id == ThreadRecord.id)
            .join(SessionRecord, RunRecord.session_id == SessionRecord.id)
            .join(bindings, bindings.c.run_id == RunRecord.id)
            .join(EnvironmentRecord, bindings.c.environment_id == EnvironmentRecord.id)
            .join(EnvironmentProviderRecord, EnvironmentRecord.provider_id == EnvironmentProviderRecord.id)
            .where(
                RunRecord.id == use.run_id,
                bindings.c.name == use.mount_name,
                RunRecord.organization_id == use.connection.organization_id,
                RunRecord.status == "running",
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
                EnvironmentRecord.device_revoked_at.is_(None),
                EnvironmentProviderRecord.type == WEBSOCKET_PROVIDER_KEY,
                EnvironmentProviderRecord.enabled.is_(True),
                EnvironmentProviderRecord.organization_id == use.connection.organization_id,
                or_(
                    EnvironmentProviderRecord.workspace_id.is_(None),
                    EnvironmentProviderRecord.workspace_id == SessionRecord.workspace_id,
                ),
            )
        )
        try:
            async with short_session(self._sessions) as session:
                selected = (await session.execute(query)).one_or_none()
                if selected is None:
                    raise EnvironmentError(
                        "The Attempt has no current accepted Environment use", code="environment_forbidden"
                    )
                run, environment, expires_at, working_directory = selected
                if working_directory is None:
                    raise EnvironmentError(
                        "The binding has no accepted working directory", code="environment_forbidden"
                    )
                await authorize_persisted_agent_principal_actions(
                    session,
                    principal=run.to_resource().authority_principal,
                    organization_id=run.organization_id,
                    workspace_id=environment.workspace_id,
                    agent_id=run.agent_id,
                    actions=frozenset({WorkspaceAction.environment_use, WorkspaceAction.agent_invoke}),
                )
                if assume_utc(expires_at) <= utc_now():
                    raise EnvironmentError(
                        "The Attempt lease expired during use admission", code="environment_forbidden"
                    )
                return working_directory
        except AuthorizationError as error:
            raise EnvironmentError("The Run Principal cannot use this mount", code="environment_forbidden") from error
        except Exception as error:
            if not is_database_unavailable(error):
                raise
            raise EnvironmentError("Mount authorization is unavailable", code="environment_unavailable") from error
