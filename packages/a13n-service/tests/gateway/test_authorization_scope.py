"""Request-local grants survive preparation, but never authorize another operation."""

import asyncio
from dataclasses import replace

import pytest
from a13n_service.iam import AuthorizationError, WorkspaceAction, authorize_agent, authorize_workspace
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.iam.operation import authorization_scope, current_operation
from a13n_service.storage import short_session, transaction
from sqlalchemy import delete, event

from tests.hooks.support import hook_actor, seed_hook_actor_access
from tests.interactions.conftest import AGENT_ID, NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID

pytestmark = pytest.mark.anyio


async def test_permissions_are_read_once_and_revocation_applies_to_next_operation(lifecycle_interaction_sessions):
    sessions = lifecycle_interaction_sessions
    await seed_hook_actor_access(sessions)
    statements = []
    async with short_session(sessions) as session:
        engine = session.get_bind()

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with authorization_scope():
            async with short_session(sessions) as session:
                await authorize_agent(
                    session,
                    actor=hook_actor(),
                    workspace_id=WORKSPACE_ID,
                    agent_id=AGENT_ID,
                    action=WorkspaceAction.run_continue,
                )
            async with transaction(sessions) as session:
                await session.execute(delete(RoleBindingRecord).where(RoleBindingRecord.resource_type == "workspace"))
            with authorization_scope():
                async with short_session(sessions) as session:
                    await authorize_agent(
                        session,
                        actor=hook_actor(),
                        workspace_id=WORKSPACE_ID,
                        agent_id=AGENT_ID,
                        action=WorkspaceAction.agent_invoke,
                    )
                    await authorize_workspace(
                        session, actor=hook_actor(), workspace_id=WORKSPACE_ID, action=WorkspaceAction.models_read
                    )
            selects = [sql for sql in statements if sql.startswith("SELECT")]
            assert sum("FROM role_bindings" in sql for sql in selects) == 1
            assert sum("FROM workspaces" in sql for sql in selects) == 1
            assert sum("agents.system_purpose" in sql for sql in selects) == 1
            assert all("FOR UPDATE" not in sql and "FOR SHARE" not in sql for sql in selects)
        assert current_operation() is None
        with authorization_scope(), pytest.raises(AuthorizationError):
            async with short_session(sessions) as session:
                await authorize_agent(
                    session,
                    actor=hook_actor(),
                    workspace_id=WORKSPACE_ID,
                    agent_id=AGENT_ID,
                    action=WorkspaceAction.agent_invoke,
                )
    finally:
        event.remove(engine, "before_cursor_execute", capture)


async def test_cached_grants_still_check_action_credential_boundary_and_principal(lifecycle_interaction_sessions):
    sessions = lifecycle_interaction_sessions
    await seed_hook_actor_access(sessions, workspace_role="viewer")
    actor = hook_actor()
    with authorization_scope():
        async with short_session(sessions) as session:
            await authorize_agent(
                session, actor=actor, workspace_id=WORKSPACE_ID, agent_id=AGENT_ID, action=WorkspaceAction.run_read
            )
            for changed, action in (
                (actor, WorkspaceAction.agent_invoke),
                (replace(actor, boundary_workspace_id="ws_other1234567890"), WorkspaceAction.run_read),
                (
                    replace(actor, credential_source="service", credential_id="ses_missing123456"),
                    WorkspaceAction.run_read,
                ),
                (
                    replace(actor, principal=actor.principal.model_copy(update={"principal_id": "usr_missing123456"})),
                    WorkspaceAction.run_read,
                ),
            ):
                with pytest.raises(AuthorizationError):
                    await authorize_agent(
                        session, actor=changed, workspace_id=WORKSPACE_ID, agent_id=AGENT_ID, action=action
                    )


async def test_scope_cleanup_covers_failure_inherited_tasks_and_concurrent_requests():
    ready, finished = asyncio.Event(), asyncio.Event()
    observed = []

    async def inherited():
        ready.set()
        await finished.wait()
        assert current_operation() is None

    with pytest.raises(RuntimeError), authorization_scope():
        task = asyncio.create_task(inherited())
        await ready.wait()
        raise RuntimeError("preparation failed")
    finished.set()
    await task

    async def request():
        with authorization_scope():
            scope = current_operation()
            observed.append(scope)
            await asyncio.sleep(0)
            assert current_operation() is scope

    await asyncio.gather(request(), request())
    assert observed[0] is not observed[1]
    assert current_operation() is None


async def test_workspace_observation_includes_direct_grants_without_granting_other_agents(
    lifecycle_interaction_sessions,
):
    sessions = lifecycle_interaction_sessions
    await seed_hook_actor_access(sessions, workspace_role="viewer")
    async with transaction(sessions) as session:
        session.add(
            RoleBindingRecord(
                id="rb_direct12345678",
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
        )
    with authorization_scope():
        async with short_session(sessions) as session:
            await authorize_workspace(
                session, actor=hook_actor(), workspace_id=WORKSPACE_ID, action=WorkspaceAction.models_read
            )
        async with transaction(sessions) as session:
            await session.execute(delete(RoleBindingRecord).where(RoleBindingRecord.resource_type == "agent"))
        async with short_session(sessions) as session:
            await authorize_agent(
                session,
                actor=hook_actor(),
                workspace_id=WORKSPACE_ID,
                agent_id=AGENT_ID,
                action=WorkspaceAction.agent_invoke,
            )
            with pytest.raises(AuthorizationError):
                await authorize_agent(
                    session,
                    actor=hook_actor(),
                    workspace_id=WORKSPACE_ID,
                    agent_id="ap_other1234567890",
                    action=WorkspaceAction.agent_invoke,
                )
