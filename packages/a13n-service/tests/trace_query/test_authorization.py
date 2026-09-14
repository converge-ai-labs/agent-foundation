from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from a13n_service.iam import WorkspaceAction
from a13n_service.iam import authorization as iam_authorization
from a13n_service.iam.models import AuthSessionRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.interactions.models import RunAttemptRecord, SessionRecord
from a13n_service.storage import transaction
from a13n_service.trace_query import TraceQueryError, TraceQueryService, TraceView
from a13n_service.trace_query import authorization as trace_authorization
from a13n_service.trace_query.authorization import RunTraceAccessAuthorizer
from a13n_service.trace_query.service import AuthorizedRunAttempt
from sqlalchemy import delete
from tests.hooks.support import hook_actor
from tests.interactions.conftest import NOW, USER_ID

from .test_service import Provider, summary

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("status", ["running", "succeeded", "yielded", "failed", "cancelled"])
async def test_authorizes_attempt_independently_of_lifecycle_state(interaction_sessions, trace_correlation, status):
    async with transaction(interaction_sessions) as database:
        attempt = await database.get(RunAttemptRecord, trace_correlation.run_attempt_id)
        attempt.status = status
        attempt.finished_at = None if status == "running" else NOW
        attempt.yield_reason = "service_drain" if status == "yielded" else None
        attempt.failure_json = (
            {"code": "test_failure", "message": "Test failure", "retry_hint": "none"} if status == "failed" else None
        )
    authorizer = RunTraceAccessAuthorizer(interaction_sessions)
    scope = await authorizer.resolve_scope(actor=hook_actor(), workspace_id=trace_correlation.workspace_id)
    decisions = await authorizer.authorize_run_attempts(
        actor=hook_actor(), scope=scope, correlations=(trace_correlation,)
    )
    assert decisions == {trace_correlation.run_attempt_id: AuthorizedRunAttempt(trace_correlation.run_attempt_id)}


@pytest.mark.parametrize(
    "field", ["organization_id", "workspace_id", "session_id", "thread_id", "run_id", "run_attempt_id", "agent_id"]
)
async def test_rejects_each_forged_correlation(interaction_sessions, trace_correlation, field):
    authorizer = RunTraceAccessAuthorizer(interaction_sessions)
    scope = await authorizer.resolve_scope(actor=hook_actor(), workspace_id=trace_correlation.workspace_id)
    assert (
        await authorizer.authorize_run_attempts(
            actor=hook_actor(), scope=scope, correlations=(trace_correlation.model_copy(update={field: "forged"}),)
        )
        == {}
    )


async def test_direct_agent_viewer_and_workspace_viewer_share_run_visibility(interaction_sessions, trace_correlation):
    async with transaction(interaction_sessions) as database:
        binding = await database.get(RoleBindingRecord, "rb_hookws717171717")
        binding.role_key = "viewer"
    authorizer = RunTraceAccessAuthorizer(interaction_sessions)
    actor = hook_actor()
    scope = await authorizer.resolve_scope(actor=actor, workspace_id=trace_correlation.workspace_id)
    assert await authorizer.authorize_run_attempts(actor=actor, scope=scope, correlations=(trace_correlation,))
    async with transaction(interaction_sessions) as database:
        binding = await database.get(RoleBindingRecord, "rb_hookws717171717")
        binding.resource_type = "agent"
        binding.resource_id = trace_correlation.agent_id
    scope = await authorizer.resolve_scope(actor=actor, workspace_id=trace_correlation.workspace_id)
    assert await authorizer.authorize_run_attempts(actor=actor, scope=scope, correlations=(trace_correlation,))
    # Another valid direct grant must not grant this Run access.
    async with transaction(interaction_sessions) as database:
        binding = await database.get(RoleBindingRecord, "rb_hookws717171717")
        binding.resource_id = "agt_other1234567890"
    assert await authorizer.authorize_run_attempts(actor=actor, scope=scope, correlations=(trace_correlation,)) == {}


async def test_checks_actual_session_workspace_not_only_backend_scope(interaction_sessions, trace_correlation):
    authorizer = RunTraceAccessAuthorizer(interaction_sessions)
    scope = await authorizer.resolve_scope(actor=hook_actor(), workspace_id=trace_correlation.workspace_id)
    async with transaction(interaction_sessions) as database:
        database.add(
            WorkspaceRecord(
                id="ws_other1234567890",
                organization_id=scope.organization_id,
                key="other",
                name="Other",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await database.flush()
        session = await database.get(SessionRecord, trace_correlation.session_id)
        session.workspace_id = "ws_other1234567890"
    assert (
        await authorizer.authorize_run_attempts(actor=hook_actor(), scope=scope, correlations=(trace_correlation,))
        == {}
    )


@pytest.mark.parametrize("change", ["grant", "membership", "principal", "workspace"])
async def test_reauthorizes_after_provider_io(interaction_sessions, trace_correlation, change):
    authorizer = RunTraceAccessAuthorizer(interaction_sessions)
    scope = await authorizer.resolve_scope(actor=hook_actor(), workspace_id=trace_correlation.workspace_id)
    async with transaction(interaction_sessions) as database:
        if change in {"grant", "membership"}:
            binding_id = "rb_hookws717171717" if change == "grant" else "rb_hookorg71717171"
            await database.execute(delete(RoleBindingRecord).where(RoleBindingRecord.id == binding_id))
        elif change == "principal":
            user = await database.get(UserRecord, USER_ID)
            user.status = "disabled"
        else:
            workspace = await database.get(WorkspaceRecord, scope.workspace_id)
            workspace.deleted_at = NOW
    assert (
        await authorizer.authorize_run_attempts(actor=hook_actor(), scope=scope, correlations=(trace_correlation,))
        == {}
    )


async def test_rejects_credential_workspace_boundary(interaction_sessions, trace_correlation):
    actor = replace(hook_actor(), boundary_workspace_id="ws_other1234567890")
    with pytest.raises(TraceQueryError, match="not found") as caught:
        await RunTraceAccessAuthorizer(interaction_sessions).resolve_scope(
            actor=actor, workspace_id=trace_correlation.workspace_id
        )
    assert caught.value.code == "trace_not_found"


async def test_service_rechecks_access_after_backend_and_conceals_detail(
    interaction_sessions, trace_correlation, monkeypatch
):
    active_sessions = 0
    session_count = 0
    original_short_session = trace_authorization.short_session

    @asynccontextmanager
    async def tracked_session(sessions):
        nonlocal active_sessions, session_count
        async with original_short_session(sessions) as database:
            active_sessions += 1
            session_count += 1
            try:
                yield database
            finally:
                active_sessions -= 1

    monkeypatch.setattr(trace_authorization, "short_session", tracked_session)

    class RevokingProvider(Provider):
        async def get_trace(self, query):
            assert active_sessions == 0
            async with transaction(interaction_sessions) as database:
                await database.execute(delete(RoleBindingRecord).where(RoleBindingRecord.id == "rb_hookws717171717"))
            return await super().get_trace(query)

    provider = RevokingProvider(items=(summary(correlation=trace_correlation),))
    service = TraceQueryService(
        provider_key="fixture", provider=provider, authorizer=RunTraceAccessAuthorizer(interaction_sessions)
    )
    with pytest.raises(TraceQueryError) as caught:
        await service.get(
            actor=hook_actor(),
            workspace_id=trace_correlation.workspace_id,
            trace_id="trace-1",
            view=TraceView.full,
        )
    assert caught.value.code == "trace_not_found"
    assert active_sessions == 0
    assert session_count == 2


@pytest.mark.parametrize("missing_action", [WorkspaceAction.trace_read, WorkspaceAction.run_read])
async def test_requires_both_trace_and_run_read(interaction_sessions, trace_correlation, monkeypatch, missing_action):
    authorizer = RunTraceAccessAuthorizer(interaction_sessions)
    scope = await authorizer.resolve_scope(actor=hook_actor(), workspace_id=trace_correlation.workspace_id)
    monkeypatch.setitem(
        iam_authorization._WORKSPACE_ROLE_ACTIONS,
        "builder",
        iam_authorization._WORKSPACE_ROLE_ACTIONS["builder"] - {missing_action},
    )
    assert (
        await authorizer.authorize_run_attempts(actor=hook_actor(), scope=scope, correlations=(trace_correlation,))
        == {}
    )
    if missing_action == WorkspaceAction.trace_read:
        with pytest.raises(TraceQueryError) as caught:
            await authorizer.resolve_scope(actor=hook_actor(), workspace_id=trace_correlation.workspace_id)
        assert caught.value.code == "trace_not_found"


async def test_rechecks_service_credential_revocation(interaction_sessions, trace_correlation):
    actor = replace(hook_actor(), credential_source="service")
    now = datetime.now(UTC)
    async with transaction(interaction_sessions) as database:
        database.add(
            AuthSessionRecord(
                id=actor.credential_id,
                user_id=USER_ID,
                token_hash="0" * 64,
                created_at=now,
                expires_at=now + timedelta(hours=1),
                revoked_at=None,
            )
        )
    authorizer = RunTraceAccessAuthorizer(interaction_sessions)
    scope = await authorizer.resolve_scope(actor=actor, workspace_id=trace_correlation.workspace_id)
    assert await authorizer.authorize_run_attempts(actor=actor, scope=scope, correlations=(trace_correlation,))
    async with transaction(interaction_sessions) as database:
        credential = await database.get(AuthSessionRecord, actor.credential_id)
        credential.revoked_at = now
    assert await authorizer.authorize_run_attempts(actor=actor, scope=scope, correlations=(trace_correlation,)) == {}
