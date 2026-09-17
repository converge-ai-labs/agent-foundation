"""Hosted root-to-child execution through real Worker, Harness, and MCP boundaries."""

import json
import traceback
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import httpx2
import pytest
from a13n_service.agents.domain import ChildAgentExecution, EffectiveAgentConfig, ResolvedSubagentEdge
from a13n_service.connectivity import execution as tool_execution
from a13n_service.connectivity.connectors.contracts import ConnectorToolOutcome
from a13n_service.connectivity.connectors.models import ConnectorProviderRecord
from a13n_service.connectivity.mcp.models import MCPConnectionRecord
from a13n_service.connectivity.mcp.transport import RemoteTransport
from a13n_service.connectivity.selection_domain import ConnectionRunSelection, ConnectionToolSelection
from a13n_service.digests import digest_request
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.process import worker as worker_composition
from a13n_service.process.control.subagent import build_subagent_maintenance
from a13n_service.secrets import SecretProtector
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from a13n_service.subagents.models import ChildRunRelationshipRecord
from anyio import create_task_group, fail_after, sleep
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits
from sqlalchemy import select

from ..connectivity.connector_helpers import FakeConnection, FakeConnectorBackend, fake_registry
from ..connectivity.selection_helpers import (
    CONNECTOR_CONNECTION_ID,
    CONNECTOR_ID,
    MCP_CONNECTION_ID,
    seed_selection_sources,
)
from ..connectivity.test_mcp_service import MCP_ENDPOINT
from ..connectivity.test_remote_execution import ToolServer
from . import test_attempt_execution as acceptance
from .conftest import MODEL_ID, ORGANIZATION_ID, USER_ID, WORKSPACE_ID, effective_agent_config
from .test_subagent_acceptance import CHILD_AGENT_ID, CHILD_REVISION_ID, _grant_and_seed_child
from .worker_helpers import worker_runtime

pytestmark = pytest.mark.anyio
CHILD_MODEL_ID = "mdl_child12345678901"


def rehash(config: EffectiveAgentConfig) -> EffectiveAgentConfig:
    return config.model_copy(
        update={
            "content_digest": digest_request(config.model_dump(mode="json", by_alias=True, exclude={"content_digest"}))
        }
    )


def frozen_graph(mode, request_limit=None):
    connector = ConnectionRunSelection(
        kind="connector",
        model_alias="conn_github",
        authorization_generation=1,
        connection_id=CONNECTOR_CONNECTION_ID,
        connector_provider_id=CONNECTOR_ID,
        tools=("issues.create",),
    )
    mcp = ConnectionRunSelection(
        kind="mcp",
        model_alias="conn_notion",
        authorization_generation=1,
        connection_id=MCP_CONNECTION_ID,
        tools=("search",),
    )
    base = effective_agent_config()
    child = rehash(
        base.model_copy(
            update={
                "resolved_model": base.resolved_model.model_copy(
                    update={
                        "execution": base.resolved_model.execution.model_copy(
                            update={
                                "model_id": CHILD_MODEL_ID,
                                "model_key": "child",
                                "upstream_model": "child-upstream",
                            }
                        ),
                        "settings": {"temperature": 0.7},
                    }
                ),
                "connection_tools": tuple(
                    ConnectionToolSelection(connection_id=item.connection_id, tools=item.tools)
                    for item in (connector, mcp)
                ),
            }
        )
    )
    edge = ResolvedSubagentEdge(
        name="researcher",
        child_agent_id=CHILD_AGENT_ID,
        child_agent_revision_id=CHILD_REVISION_ID,
        context={"include_task": True, "history": "none", "task_state": "shared"},
        environment={"mode": "none"},
        usage_limits=UsageLimits(request_limit=request_limit),
    )
    return rehash(
        base.model_copy(
            update={
                "subagent_mode": mode,
                "resolved_subagents": (edge,),
                "child_configs": {
                    CHILD_REVISION_ID: ChildAgentExecution(
                        agent_id=CHILD_AGENT_ID,
                        revision_content_digest="3" * 64,
                        effective_config=child,
                        connection_selections=(connector, mcp),
                    )
                },
            }
        )
    )


@pytest.mark.parametrize(("mode", "request_limit"), [("inline", None), ("async", None), ("async", 1)])
async def test_worker_child_uses_own_model_and_tools_and_delivers_result(
    interaction_sessions,
    interaction_object_store,
    tmp_path,
    monkeypatch,
    mode,
    request_limit,
    caplog,
):
    parse_contexts = Mock(wraps=tool_execution.parse_native_contexts)
    monkeypatch.setattr(tool_execution, "parse_native_contexts", parse_contexts)
    failures = []
    original_failure = RunAttemptControl.fail_execution

    async def capture_failure(self, *args, **kwargs):
        failures.append(traceback.format_exc())
        return await original_failure(self, *args, **kwargs)

    monkeypatch.setattr(RunAttemptControl, "fail_execution", capture_failure)
    config = frozen_graph(mode, request_limit)
    await _grant_and_seed_child(interaction_sessions)
    await seed_selection_sources(
        interaction_sessions,
        organization_id=ORGANIZATION_ID,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
    )
    protector = SecretProtector(key=b"k" * 32, encryption_key_id="test")
    async with transaction(interaction_sessions) as database:
        (await database.get(RoleBindingRecord, "rbac_3333333333333333")).role_key = "admin"
        provider = await database.get(ConnectorProviderRecord, CONNECTOR_ID)
        provider.configuration_json = {"endpoint": "https://connector.example", "tenant": "tenant-1"}
        provider.replace_credential('{"api_key":"secret"}', protector)
        (await database.get(MCPConnectionRecord, MCP_CONNECTION_ID)).endpoint_url = MCP_ENDPOINT
    monkeypatch.setattr(acceptance, "effective_agent_config", lambda: config)
    states, parent, _ = await acceptance._accept_root(interaction_sessions, interaction_object_store)
    server = ToolServer()
    original_mcp = worker_composition.build_mcp_clients

    def mcp_resources(settings, sessions, protector, http, endpoints):
        resources = original_mcp(settings, sessions, protector, http, endpoints)
        return replace(
            resources,
            transport=RemoteTransport(settings.connectivity_endpoint_policy(), transport=httpx2.MockTransport(server)),
        )

    monkeypatch.setattr(worker_composition, "build_mcp_clients", mcp_resources)
    connector_calls = AsyncMock(return_value=ConnectorToolOutcome(kind="succeeded", result={"created": True}))
    monkeypatch.setattr(FakeConnection, "execute_tool", connector_calls)
    observed_models = []
    parent_requests = []
    child_requests = []

    async def parent_model(messages, info):
        names = {tool.name for tool in info.function_tools}
        assert "delegate" in names
        assert not any(name.startswith(("mcp_", "connector_")) for name in names)
        parent_requests.append(messages)
        if len(parent_requests) == 1:
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps(
                        {"subagent" if mode == "inline" else "subagent_name": "researcher", "prompt": "Use both tools"}
                    ),
                    tool_call_id="delegate-child",
                )
            }
        else:
            yield "parent complete"

    async def child_model(messages, info):
        assert info.model_settings["temperature"] == 0.7
        child_requests.append(messages)
        if len(child_requests) == 1:
            assert len(info.function_tools) == 2
            yield {
                index: DeltaToolCall(
                    name=tool.name,
                    json_args='{"title":"test","query":"test"}',
                    tool_call_id=f"child-tool-{index}",
                )
                for index, tool in enumerate(info.function_tools)
            }
        else:
            yield "child complete"

    async def build(snapshot, _provider):
        observed_models.append(snapshot.model_id)
        return FunctionModel(stream_function=parent_model if snapshot.model_id == MODEL_ID else child_model)

    factory = Mock(spec=NativeModelFactory)
    factory.build.side_effect = build
    settings = Settings(
        service={"build_version": "test"},
        worker={"concurrency": 1, "poll_interval_seconds": 0.02},
        subagents={"reconcile_poll_interval_seconds": 0.02},
    )
    async with worker_runtime(
        interaction_sessions,
        interaction_object_store,
        tmp_path,
        monkeypatch,
        settings=settings,
        model_factory=factory,
        connectors=fake_registry(FakeConnectorBackend()),
    ) as (runtime, shared):
        loop = runtime.execution_loop
        assert loop is not None
        maintenance = build_subagent_maintenance(settings, shared, runtime.run_display)
        with fail_after(20):
            async with create_task_group() as tasks:
                tasks.start_soon(loop.run)
                polls = 0
                while True:
                    polls += 1
                    await maintenance.reconcile_once()
                    async with short_session(interaction_sessions) as database:
                        root = await database.get(RunRecord, parent.id)
                        assert root.status != "failed", "\n".join(failures)
                        children = (
                            await database.scalars(select(RunRecord).where(RunRecord.agent_id == CHILD_AGENT_ID))
                        ).all()
                        if request_limit is None:
                            assert not any(child.status == "failed" for child in children), [
                                child.failure_json for child in children
                            ]
                        entries = (
                            await database.scalars(
                                select(ThreadInboxRecord).where(ThreadInboxRecord.kind == "async_subagent_result")
                            )
                        ).all()
                        assert polls < 250, (
                            root.status,
                            [(c.id, c.status) for c in children],
                            [(e.status, e.target_run_id) for e in entries],
                            len(parent_requests),
                            len(child_requests),
                            failures,
                        )
                        successor = (
                            await database.get(RunRecord, entries[0].target_run_id)
                            if entries and entries[0].target_run_id
                            else None
                        )
                        if root.status == "completed" and (
                            mode == "inline"
                            or (
                                entries
                                and entries[0].status == "consumed"
                                and successor is not None
                                and successor.status == "completed"
                            )
                        ):
                            break
                    await sleep(0.02)
                await loop.drain()
                await loop.wait_stopped()
                tasks.cancel_scope.cancel()
        assert "Harness live observation projection failed" not in caplog.text
        # Validate each scope before admission, then reconstruct it once for
        # execution. Individual tool requests must not reparse retained context.
        assert parse_contexts.call_count == (4 if mode == "inline" else 6)
        assert set(observed_models) == {MODEL_ID, CHILD_MODEL_ID}, repr(parent_requests)
        assert len(child_requests) == (2 if request_limit is None else 1)
        connector_calls.assert_awaited_once()
        assert server.calls == [(None, "search")]
        async with short_session(interaction_sessions) as database:
            relationships = (await database.scalars(select(ChildRunRelationshipRecord))).all()
            assert len(relationships) == (1 if mode == "async" else 0)
            if mode == "async":
                child = await database.get(RunRecord, relationships[0].child_run_id)
                if request_limit is None:
                    assert child.output_json == "child complete"
                else:
                    assert child.status == "failed"
                    assert child.failure_json is not None
                successor = await database.get(RunRecord, entries[0].target_run_id)
                assert successor.status == "completed"
                assert successor.id != parent.id
        if mode == "async":
            child_state = await states.read(child.organization_id, child.id)
            assert child_state.envelope.effective_agent_config.resolved_model.execution.model_id == CHILD_MODEL_ID

            assert child_state.envelope.usage_limits.request_limit == (1000 if request_limit is None else 1)


@pytest.mark.parametrize("refresh", ["healthy", "revoked", "unavailable"])
async def test_inline_and_root_share_ten_loop_iam_refresh(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, caplog, refresh
):
    from a13n_service.iam import attempts as iam_attempts
    from a13n_service.iam.models import UserRecord
    from a13n_service.interactions.models import RunAttemptRecord
    from sqlalchemy.exc import OperationalError

    caplog.set_level("INFO")
    await _grant_and_seed_child(interaction_sessions)
    config = frozen_graph("inline")
    child = config.child_configs[CHILD_REVISION_ID]
    config = rehash(
        config.model_copy(
            update={
                "child_configs": {
                    CHILD_REVISION_ID: child.model_copy(
                        update={
                            "effective_config": rehash(
                                child.effective_config.model_copy(update={"connection_tools": ()})
                            ),
                            "connection_selections": (),
                        }
                    )
                }
            }
        )
    )
    monkeypatch.setattr(acceptance, "effective_agent_config", lambda: config)
    _, parent, _ = await acceptance._accept_root(interaction_sessions, interaction_object_store)
    requests = []
    reads = AsyncMock(wraps=iam_attempts.read_principal_permissions)
    monkeypatch.setattr(iam_attempts, "read_principal_permissions", reads)

    async def model(messages, info, *, child):
        requests.append("child" if child else "root")
        if len(requests) == 10:
            if refresh == "revoked":
                async with transaction(interaction_sessions) as session:
                    (await session.get(UserRecord, USER_ID)).status = "disabled"
            elif refresh == "unavailable":
                reads.side_effect = OperationalError("SELECT", {}, OSError("database unavailable"))
        if child or len(requests) == 11:
            yield "done"
        else:
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps({"subagent": "researcher", "prompt": "Reply once."}),
                    tool_call_id=f"child-{len(requests)}",
                )
            }

    async def build(snapshot, _provider):
        async def stream(messages, info):
            async for item in model(messages, info, child=snapshot.model_id == CHILD_MODEL_ID):
                yield item

        return FunctionModel(stream_function=stream)

    factory = Mock(spec=NativeModelFactory)
    factory.build.side_effect = build
    settings = Settings(service={"build_version": "test"}, worker={"concurrency": 1, "poll_interval_seconds": 0.02})
    async with worker_runtime(
        interaction_sessions, interaction_object_store, tmp_path, monkeypatch, settings=settings, model_factory=factory
    ) as (runtime, _):
        loop = runtime.execution_loop
        with fail_after(20):
            async with create_task_group() as tasks:
                tasks.start_soon(loop.run)
                while True:
                    async with short_session(interaction_sessions) as session:
                        attempt = await session.scalar(
                            select(RunAttemptRecord).where(RunAttemptRecord.run_id == parent.id)
                        )
                        if attempt is not None and attempt.status in {"succeeded", "failed"}:
                            row = await session.get(RunRecord, parent.id)
                            break
                    await sleep(0.02)
                await loop.drain()
                await loop.wait_stopped()
                tasks.cancel_scope.cancel()
    assert requests == ["root", "child"] * 5 + (["root"] if refresh == "healthy" else [])
    assert reads.await_count == 2
    assert attempt.status == ("succeeded" if refresh == "healthy" else "failed")
    if refresh == "healthy":
        assert row.status == "completed"
        assert [
            record.model_requests for record in caplog.records if record.message == "run_attempt_permissions_refreshed"
        ] == [0, 10]
    else:
        assert attempt.failure_json["code"] == (
            "attempt_authorization_denied" if refresh == "revoked" else "attempt_dependency_unavailable"
        )
