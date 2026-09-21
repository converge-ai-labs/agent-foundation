"""Process-local IAM snapshots refreshed at bounded Agent model-loop boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from a13n_harness.errors import RunError
from a13n_logging import get_logger
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.environments.authorization import EnvironmentAuthorization, read_environment_authorization
from a13n_service.environments.mount_models import RunEnvironmentMountRecord
from a13n_service.storage import short_session

from .authorization import PrincipalPermissions, WorkspaceAction, read_principal_permissions
from .domain import AuthorizationError, PrincipalRef

logger = get_logger(__name__)
_LOOPS_PER_REFRESH = 10


class RootExecutionPolicy(Protocol):
    """Trusted source-specific authority, evaluated inside the shared IAM refresh."""

    @property
    def required_actions(self) -> frozenset[WorkspaceAction]: ...

    async def refresh(self, session: AsyncSession, snapshot: PrincipalPermissions) -> None: ...


class AttemptAuthorizationError(RunError):
    """A terminal IAM failure that must bypass Harness model recovery."""

    def __init__(self, code: str) -> None:
        super().__init__(code, code=code)


@dataclass(frozen=True, slots=True)
class _ExecutionScope:
    principal: PrincipalRef
    organization_id: str
    workspace_id: str
    root_agent_id: str
    agent_ids: frozenset[str]
    run_id: str
    run_attempt_id: str
    environment_id: str | None
    root_policy: RootExecutionPolicy | None


class AttemptAuthorization:
    """Own one Attempt's current permissions and shared root/inline request cadence."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: async_sessionmaker[AsyncSession] | None = None
        self._scope: _ExecutionScope | None = None
        self._snapshot: PrincipalPermissions | None = None
        self._environments: dict[str, EnvironmentAuthorization] = {}
        self._failure: str | None = None
        self._model_requests = 0
        self._requests_since_refresh = 0

    @property
    def snapshot(self) -> PrincipalPermissions:
        """Capture the latest complete snapshot, never an invalidated predecessor."""
        self.raise_if_failed()
        if self._snapshot is None:
            raise AttemptAuthorizationError("attempt_authorization_unprepared")
        return self._snapshot

    def raise_if_failed(self) -> None:
        """Preserve a fatal IAM decision even if Harness converts its exception into a result."""
        if self._failure is not None:
            raise AttemptAuthorizationError(self._failure)

    def require_environment(self, environment_id: str) -> None:
        """Check an admitted binding and latest permissions without database I/O."""
        self._require_environment(self._environments.get(environment_id))

    def _require_environment(self, environment: EnvironmentAuthorization | None) -> None:
        snapshot = self.snapshot
        scope = self._scope
        assert scope is not None
        if environment is None:
            raise AuthorizationError("environment_not_found", concealed=True)
        if not environment.enabled:
            raise AuthorizationError("environment_provider_unavailable", concealed=True)
        required = {WorkspaceAction.environment_use, WorkspaceAction.agent_invoke}
        if not required.issubset(snapshot.for_agent(scope.root_agent_id)):
            raise AuthorizationError("permission_denied", concealed=True)

    async def admit_environment(self, *, name: str, environment_id: str) -> None:
        """Admit an immutable accepted addition before preparing its runtime adapter."""
        async with self._lock:
            self.raise_if_failed()
            scope, sessions = self._scope, self._sessions
            if scope is None or sessions is None:
                raise AttemptAuthorizationError("attempt_authorization_unprepared")
            await self._refresh()
            async with short_session(sessions) as session:
                mount = await session.get(RunEnvironmentMountRecord, (scope.run_id, name))
                if mount is None or (mount.organization_id, mount.workspace_id, mount.environment_id) != (
                    scope.organization_id,
                    scope.workspace_id,
                    environment_id,
                ):
                    raise AuthorizationError("environment_not_found", concealed=True)
                environment = await read_environment_authorization(
                    session,
                    environment_id=environment_id,
                    organization_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                    previous=None,
                )
            self._require_environment(environment)
            self._environments[environment_id] = environment

    async def initialize(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        principal: PrincipalRef,
        organization_id: str,
        workspace_id: str,
        root_agent_id: str,
        agent_ids: frozenset[str],
        run_id: str,
        run_attempt_id: str,
        environment_id: str | None = None,
        root_policy: RootExecutionPolicy | None = None,
    ) -> None:
        """Read IAM before preparation effects; this owner cannot be rebound or reset."""
        async with self._lock:
            if self._scope is not None:
                raise RuntimeError("Attempt authorization has already been initialized")
            self._sessions = sessions
            self._scope = _ExecutionScope(
                principal,
                organization_id,
                workspace_id,
                root_agent_id,
                agent_ids | {root_agent_id},
                run_id,
                run_attempt_id,
                environment_id,
                root_policy,
            )
            await self._refresh()

    async def admit_model_request(self, *, agent_id: str | None = None) -> None:
        """Refresh before request 11, 21, ... across concurrent root and inline calls."""
        async with self._lock:
            snapshot = self.snapshot
            scope = self._scope
            assert scope is not None
            selected = scope.root_agent_id if agent_id is None else agent_id
            if selected not in scope.agent_ids:
                raise AuthorizationError("permission_denied", concealed=True)
            if self._requests_since_refresh == _LOOPS_PER_REFRESH:
                await self._refresh()
                snapshot = self.snapshot
            required = self._root_actions() if selected == scope.root_agent_id else {WorkspaceAction.agent_invoke}
            if not required.issubset(snapshot.for_agent(selected)):
                raise AuthorizationError("permission_denied", concealed=True)
            self._model_requests += 1
            self._requests_since_refresh += 1

    def _root_actions(self) -> frozenset[WorkspaceAction]:
        assert self._scope is not None
        policy = self._scope.root_policy
        return policy.required_actions if policy is not None else frozenset({WorkspaceAction.agent_invoke})

    async def _refresh(self) -> None:
        scope, sessions = self._scope, self._sessions
        assert scope is not None and sessions is not None
        try:
            async with short_session(sessions) as session:
                snapshot = await read_principal_permissions(
                    session,
                    principal=scope.principal,
                    organization_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                )
                if scope.root_policy is not None:
                    await scope.root_policy.refresh(session, snapshot)
                environment_ids = set(self._environments)
                if scope.environment_id is not None:
                    environment_ids.add(scope.environment_id)
                environments = {
                    environment_id: await read_environment_authorization(
                        session,
                        environment_id=environment_id,
                        organization_id=scope.organization_id,
                        workspace_id=scope.workspace_id,
                        previous=self._environments.get(environment_id),
                    )
                    for environment_id in sorted(environment_ids)
                }
            if not self._root_actions().issubset(snapshot.for_agent(scope.root_agent_id)):
                raise AuthorizationError("permission_denied", concealed=True)
        except AuthorizationError as error:
            self._failure = "attempt_authorization_denied"
            raise AttemptAuthorizationError(self._failure) from error
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            self._failure = "attempt_dependency_unavailable"
            raise AttemptAuthorizationError(self._failure) from error
        self._snapshot = snapshot
        self._environments = environments
        self._requests_since_refresh = 0
        logger.info(
            "run_attempt_permissions_refreshed",
            extra={
                "run_id": scope.run_id,
                "run_attempt_id": scope.run_attempt_id,
                "model_requests": self._model_requests,
            },
        )
