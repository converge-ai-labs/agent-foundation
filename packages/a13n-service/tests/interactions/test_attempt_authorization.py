"""Attempt IAM cadence, permission changes, and failure isolation on real storage."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from a13n_service.iam import AuthorizationError, PrincipalRef, WorkspaceAction, authorize_agent, authorize_workspace
from a13n_service.iam.attempts import AttemptAuthorization, AttemptAuthorizationError
from a13n_service.iam.models import RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.storage import short_session, transaction
from sqlalchemy import delete, event
from sqlalchemy.exc import OperationalError

from tests.hooks.support import hook_actor, seed_hook_actor_access

from .conftest import AGENT_ID, NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID

pytestmark = pytest.mark.anyio
CHILD_ID = "agt_inline71717171"


async def initialize(sessions, *, agent_ids=frozenset({CHILD_ID})):
    authorization = AttemptAuthorization()
    await authorization.initialize(
        sessions,
        principal=hook_actor().principal,
        organization_id=ORGANIZATION_ID,
        workspace_id=WORKSPACE_ID,
        root_agent_id=AGENT_ID,
        agent_ids=agent_ids,
        run_id="run_permissions7171",
        run_attempt_id="ratt_permissions7171",
    )
    return authorization


async def admit(authorization, count):
    for _ in range(count):
        await authorization.admit_model_request()


def direct_runner():
    return RoleBindingRecord(
        id="rb_direct71717171",
        organization_id=ORGANIZATION_ID,
        workspace_id=WORKSPACE_ID,
        principal_type="user",
        principal_id=USER_ID,
        resource_type="agent",
        resource_id=AGENT_ID,
        role_key="runner",
        created_by_user_id=USER_ID,
        created_at=NOW,
        updated_at=NOW,
    )


async def test_tenth_loop_reuses_permissions_and_eleventh_refreshes(interaction_sessions, caplog):
    caplog.set_level("INFO")
    sessions = interaction_sessions
    await seed_hook_actor_access(sessions)
    authorization = await initialize(sessions)
    first = authorization.snapshot
    async with transaction(sessions) as session:
        session.add(direct_runner())
        binding = await session.get(RoleBindingRecord, "rb_hookws717171717")
        binding.role_key = "viewer"
    await admit(authorization, 10)
    assert authorization.snapshot is first
    assert WorkspaceAction.environment_use in first.workspace_actions
    await authorization.admit_model_request()
    refreshed = authorization.snapshot
    assert refreshed is not first
    assert WorkspaceAction.environment_use not in refreshed.workspace_actions
    assert WorkspaceAction.agent_invoke in refreshed.for_agent(AGENT_ID)
    assert WorkspaceAction.agent_invoke not in refreshed.for_agent(CHILD_ID)
    execution_actor = replace(hook_actor(), auth_method="internal")
    async with short_session(sessions) as session:
        with pytest.raises(AuthorizationError, match="permission_denied"):
            await authorize_workspace(
                session,
                actor=execution_actor,
                workspace_id=WORKSPACE_ID,
                action=WorkspaceAction.environment_use,
                snapshot=refreshed,
            )
        await authorize_workspace(
            session,
            actor=execution_actor,
            workspace_id=WORKSPACE_ID,
            action=WorkspaceAction.run_read,
            snapshot=refreshed,
        )
    with pytest.raises(AuthorizationError, match="permission_denied"):
        await authorization.admit_model_request(agent_id=CHILD_ID)
    await admit(authorization, 9)
    assert authorization.snapshot is refreshed
    await authorization.admit_model_request()
    assert authorization.snapshot is not refreshed
    refreshes = [record for record in caplog.records if record.message == "run_attempt_permissions_refreshed"]
    assert [record.model_requests for record in refreshes] == [0, 10, 20]


@pytest.mark.parametrize("change", ["principal", "membership", "invocation"])
async def test_required_authority_loss_fails_closed_at_refresh(interaction_sessions, change):
    sessions = interaction_sessions
    await seed_hook_actor_access(sessions)
    authorization = await initialize(sessions)
    async with transaction(sessions) as session:
        if change == "principal":
            (await session.get(UserRecord, USER_ID)).status = "disabled"
        elif change == "membership":
            await session.execute(delete(RoleBindingRecord).where(RoleBindingRecord.resource_type == "organization"))
        else:
            (await session.get(RoleBindingRecord, "rb_hookws717171717")).role_key = "viewer"
    await admit(authorization, 10)
    assert WorkspaceAction.agent_invoke in authorization.snapshot.for_agent(AGENT_ID)
    with pytest.raises(AttemptAuthorizationError, match="attempt_authorization_denied"):
        await authorization.admit_model_request()
    with pytest.raises(AttemptAuthorizationError, match="attempt_authorization_denied"):
        _ = authorization.snapshot
    with pytest.raises(AttemptAuthorizationError, match="attempt_authorization_denied"):
        await initialize(sessions)


async def test_concurrent_root_and_inline_requests_share_the_refresh(interaction_sessions, monkeypatch):
    from a13n_service.iam import attempts

    sessions = interaction_sessions
    await seed_hook_actor_access(sessions)
    load = AsyncMock(wraps=attempts.read_principal_permissions)
    monkeypatch.setattr(attempts, "read_principal_permissions", load)
    authorization = await initialize(sessions)
    await asyncio.gather(
        *(authorization.admit_model_request(agent_id=CHILD_ID if index % 2 else None) for index in range(31))
    )
    assert load.await_count == 4
    replacement = await initialize(sessions)
    assert load.await_count == 5
    await admit(replacement, 10)
    assert load.await_count == 5
    await replacement.admit_model_request()
    assert load.await_count == 6


async def test_failed_database_refresh_cannot_fall_back_to_old_permissions(interaction_sessions, monkeypatch):
    from a13n_service.iam import attempts

    sessions = interaction_sessions
    await seed_hook_actor_access(sessions)
    authorization = await initialize(sessions)
    await admit(authorization, 10)
    load = AsyncMock(side_effect=OperationalError("SELECT", {}, OSError("database unavailable")))
    monkeypatch.setattr(attempts, "read_principal_permissions", load)
    for _ in range(2):
        with pytest.raises(AttemptAuthorizationError, match="attempt_dependency_unavailable"):
            await authorization.admit_model_request()
        with pytest.raises(AttemptAuthorizationError, match="attempt_dependency_unavailable"):
            _ = authorization.snapshot
    assert load.await_count == 1


async def test_cached_checks_keep_scope_and_observe_workspace_deletion_at_refresh(interaction_sessions):
    sessions = interaction_sessions
    await seed_hook_actor_access(sessions)
    authorization = await initialize(sessions)
    snapshot = authorization.snapshot
    internal = replace(hook_actor(), auth_method="internal")
    async with short_session(sessions) as session:
        for actor in (
            hook_actor(),
            replace(internal, principal=PrincipalRef(principal_type="user", principal_id="usr_somebodyelse71717")),
        ):
            with pytest.raises(AuthorizationError, match="permission_snapshot_scope_mismatch"):
                await authorize_agent(
                    session,
                    actor=actor,
                    workspace_id=WORKSPACE_ID,
                    agent_id=AGENT_ID,
                    action=WorkspaceAction.agent_invoke,
                    snapshot=snapshot,
                )
    async with transaction(sessions) as session:
        (await session.get(WorkspaceRecord, WORKSPACE_ID)).deleted_at = NOW
    async with short_session(sessions) as session:
        await authorize_workspace(
            session,
            actor=internal,
            workspace_id=WORKSPACE_ID,
            action=WorkspaceAction.environment_use,
            snapshot=authorization.snapshot,
        )
    await admit(authorization, 10)
    with pytest.raises(AttemptAuthorizationError, match="attempt_authorization_denied"):
        await authorization.admit_model_request()
    with pytest.raises(AttemptAuthorizationError, match="attempt_authorization_denied"):
        _ = authorization.snapshot
    with pytest.raises(AttemptAuthorizationError, match="attempt_authorization_denied"):
        await initialize(sessions)


async def test_repeated_operations_do_not_reread_principal_or_roles(interaction_sessions):
    sessions = interaction_sessions
    await seed_hook_actor_access(sessions)
    authorization = await initialize(sessions)
    statements = []
    async with short_session(sessions) as session:
        engine = session.bind.sync_engine

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", record)
    try:
        for _ in range(20):
            async with short_session(sessions) as session:
                await authorize_workspace(
                    session,
                    actor=replace(hook_actor(), auth_method="internal"),
                    workspace_id=WORKSPACE_ID,
                    action=WorkspaceAction.environment_use,
                    snapshot=authorization.snapshot,
                )
        assert statements == []
        await admit(authorization, 11)
        assert sum("role_bindings" in statement for statement in statements) == 1
        assert sum("from users" in statement for statement in statements) == 1
    finally:
        event.remove(engine, "before_cursor_execute", record)
