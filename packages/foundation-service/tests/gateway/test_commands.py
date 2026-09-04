from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_service.agents.invocation_resolution import FrozenAgentInvocation
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.gateway.commands import GatewayCommandError, NativeInteractionCommands, StartRunRequest
from a13n_service.iam import AuthenticatedActor, PrincipalRef, PrincipalType
from a13n_service.interactions import MCPToolSnapshotRef, RunAcceptanceService, RunPayloadStore, RunStateStore
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.storage import short_session
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.interactions.conftest import (
    AGENT_ID,
    AGENT_REVISION_ID,
    NOW,
    TENANT_ID,
    USER_ID,
    WORKSPACE_ID,
    effective_agent_config,
)
from tests.interactions.test_acceptance import _inline_hooks

pytestmark = pytest.mark.anyio


def _actor() -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type=PrincipalType.user, principal_id=USER_ID),
        auth_method="session",
        credential_id="ses_gateway_test",
        boundary_workspace_id=WORKSPACE_ID,
    )


def _frozen(*, runtime_lock_digest: str = "a" * 64) -> FrozenAgentInvocation:
    config = effective_agent_config().model_copy(update={"runtime_lock_digest": runtime_lock_digest})
    return FrozenAgentInvocation(
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        selector_kind="current",
        effective_config=config,
        sensitive_values={},
        sensitive_values_digest="0" * 64,
        connector_connection_selections=(),
        mcp_connection_selections=(),
        mcp_tool_snapshot=MCPToolSnapshotRef(
            digest_sha256="d" * 64,
            size_bytes=2,
            content_type="application/vnd.a13n.mcp-tool-snapshot+json",
            schema_version="1",
        ),
    )


class _Preparation:
    def __init__(self) -> None:
        self.calls = 0

    async def prepare(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(organization_id=TENANT_ID)


class _Freezing:
    def __init__(self, values: list[FrozenAgentInvocation]) -> None:
        self._values = values
        self.calls = 0

    async def freeze_in_transaction(self, _database, *, prepared):
        del prepared
        selected = self._values[min(self.calls, len(self._values) - 1)]
        self.calls += 1
        return selected


def _request(text: str = "hello") -> StartRunRequest:
    return StartRunRequest.model_validate(
        {
            "agent_id": AGENT_ID,
            "input": {"schema_version": "2", "content": [{"type": "text", "text": text}]},
        }
    )


def _commands(
    sessions: async_sessionmaker[AsyncSession],
    objects,
    preparation: _Preparation,
    freezing: _Freezing,
) -> NativeInteractionCommands:
    resolver = SimpleNamespace(preparation=preparation, freezing=freezing)
    acceptance = RunAcceptanceService(
        sessions,
        RunStateStore(objects),
        RunPayloadStore(objects),
        _inline_hooks(),
        clock=lambda: NOW,
    )
    return NativeInteractionCommands(
        sessions,
        resolver,
        acceptance,
        RunStateStore(objects),
        AsyncMock(),
        EndpointPolicy(),
        clock=lambda: NOW,
    )


async def test_start_accepts_root_run_and_replays_before_resolution(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_sessions = lifecycle_interaction_sessions
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    preparation = _Preparation()
    freezing = _Freezing([_frozen()])
    commands = _commands(interaction_sessions, interaction_object_store, preparation, freezing)

    first = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="start-one",
        request=_request(),
    )
    repeated = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="start-one",
        request=_request(),
    )

    assert repeated == first
    assert preparation.calls == 1
    assert freezing.calls == 2
    assert first.thread_id.startswith("thread_")
    async with short_session(interaction_sessions) as database:
        run = await database.scalar(select(RunRecord).where(RunRecord.id == first.run_id))
        thread = await database.scalar(select(ThreadRecord).where(ThreadRecord.id == first.thread_id))
        session = await database.scalar(select(SessionRecord).where(SessionRecord.id == first.session_id))
    assert run is not None and run.input_text == "hello"
    assert thread is not None and thread.current_run_id == run.id
    assert session is not None and session.workspace_id == WORKSPACE_ID


async def test_start_rejects_idempotency_key_reuse_with_changed_request(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_sessions = lifecycle_interaction_sessions
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(interaction_sessions, interaction_object_store, _Preparation(), _Freezing([_frozen()]))
    await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="start-conflict",
        request=_request("first"),
    )

    with pytest.raises(GatewayCommandError) as captured:
        await commands.start(
            actor=_actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="start-conflict",
            request=_request("different"),
        )

    assert captured.value.code == "idempotency_conflict"
    assert captured.value.status_code == 409


async def test_start_rejects_final_invocation_drift_without_committing_run(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_sessions = lifecycle_interaction_sessions
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(
        interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([_frozen(), _frozen(runtime_lock_digest="b" * 64)]),
    )

    with pytest.raises(GatewayCommandError) as captured:
        await commands.start(
            actor=_actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="start-drift",
            request=_request(),
        )

    assert captured.value.code == "run_invocation_changed"
    async with short_session(interaction_sessions) as database:
        count = len(tuple((await database.scalars(select(RunRecord).where(RunRecord.input_text == "hello"))).all()))
    assert count == 0
