"""Default injection through the real Harness MCP path, outside Agent selections."""

import json
from dataclasses import replace

import httpx2
import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.execution import AttemptToolScope
from a13n_service.connectivity.mcp.transport import RemoteTransport
from a13n_service.connectivity.native_context import AccountRunContext, bind_account_tools, parse_native_contexts
from a13n_service.connectivity.selection_resolution import FrozenRunConnectivity
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.storage import transaction
from pydantic import ValidationError
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .conftest import ACCOUNT_ID, ORG_ID, WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


@pytest.fixture
async def native_runtime(
    connectivity_sessions, credential_protector, monkeypatch, external_runtime_factory, execution_authorization
):
    async with transaction(connectivity_sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT_ID)
        account.provider_key = "slack"
        account.replace_credential('{"bot_token":"first"}', credential_protector)
        context = await bind_account_tools(
            session,
            actor=actor(),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            account_id=ACCOUNT_ID,
            allowed_actions=("slack.send_message",),
            target_scope={"channel_ids": ["C1"]},
        )
    scope = AttemptToolScope(
        replace(actor(), auth_method="internal"),
        ORG_ID,
        WORKSPACE_ID,
        FrozenRunConnectivity(()),
        (context,),
        authorization=await execution_authorization(),
    )
    current = [scope]

    async def read_scope(_attempt, *, accepted=None):
        return current[0]

    policy = EndpointPolicy()
    requests = []

    def send(request):
        requests.append((request.headers["authorization"], json.loads(request.content)))
        return httpx2.Response(200, json={"ok": True, "channel": "C1", "ts": "1.0"})

    runtime = external_runtime_factory(
        ConnectorProviderRegistry(()), RemoteTransport(policy), policy, transport=httpx2.MockTransport(send)
    )
    monkeypatch.setattr(runtime, "_scope", read_scope)
    return runtime, current, requests


def harness(destination="C1"):
    async def model(messages, info):
        if messages[-1].parts[0].part_kind == "tool-return":
            yield "done"
        else:
            assert len(info.function_tools) == 1
            tool = info.function_tools[0]
            assert "account_id" not in tool.parameters_json_schema["properties"]
            yield {
                0: DeltaToolCall(
                    name=tool.name,
                    json_args=json.dumps({"channel_id": destination, "text": "hello"}),
                    tool_call_id="send-1",
                )
            }

    return HarnessBuilder().build(AgentSpec(), model=FunctionModel(stream_function=model), output_type=str)


async def test_default_tools_survive_empty_user_selections_and_refresh_credentials(
    native_runtime,
    connectivity_sessions,
    credential_protector,
):
    runtime, _, requests = native_runtime
    async with runtime.capabilities(lambda: None) as capabilities:
        assert len(capabilities) == 1
        first_key = capabilities[0].id
        agent = harness()
        await agent.run("send", bindings=RunBindings.embedded(capabilities=capabilities))
        async with transaction(connectivity_sessions) as session:
            account = await session.get(AccountRecord, ACCOUNT_ID)
            account.replace_credential('{"bot_token":"rotated"}', credential_protector)
        await agent.run("send again", bindings=RunBindings.embedded(capabilities=capabilities))
    async with runtime.capabilities(lambda: None) as replacement:
        assert replacement[0].id == first_key
        await agent.run("replacement Attempt", bindings=RunBindings.embedded(capabilities=replacement))
    assert [auth for auth, _ in requests] == ["Bearer first", "Bearer rotated", "Bearer rotated"]
    assert all(body == {"channel": "C1", "text": "hello"} for _, body in requests)


@pytest.mark.parametrize("revocation", ["account", "iam", "scope", "principal", "deleted"])
async def test_default_call_reauthorizes_before_dispatch(native_runtime, connectivity_sessions, revocation):
    runtime, current, requests = native_runtime
    async with runtime.capabilities(lambda: None) as capabilities:
        async with transaction(connectivity_sessions) as session:
            account = await session.get(AccountRecord, ACCOUNT_ID)
            if revocation == "account":
                account.status = "disabled"
            elif revocation == "deleted":
                from .conftest import NOW

                account.clear_credential()
                account.status = "disabled"
                account.deleted_at = NOW
            elif revocation == "iam":
                role = await session.get(RoleBindingRecord, "rb_connectivity_admin")
                role.role_key = "viewer"
            elif revocation == "scope":
                current[0] = replace(current[0], native_tool_contexts=())
            else:
                context = current[0].native_tool_contexts[0]
                current[0] = replace(
                    current[0],
                    native_tool_contexts=(
                        context.model_copy(
                            update={
                                "execution_principal_ref": context.execution_principal_ref.model_copy(
                                    update={"principal_id": "other"}
                                )
                            }
                        ),
                    ),
                )
        if revocation == "iam":
            from a13n_service.iam.attempts import AttemptAuthorizationError

            await harness().run("send", bindings=RunBindings.embedded(capabilities=capabilities))
            assert len(requests) == 1
            requests.clear()
            for _ in range(10):
                await current[0].authorization.admit_model_request()
            with pytest.raises(AttemptAuthorizationError, match="attempt_authorization_denied"):
                await current[0].authorization.admit_model_request()
        with pytest.raises(
            Exception,
            match=r"native_source_unavailable|not found|permission_denied|external_tool_scope_changed|attempt_authorization_denied",
        ):
            await harness().run("send", bindings=RunBindings.embedded(capabilities=capabilities))
    assert requests == []


async def test_target_arguments_cannot_expand_default_scope(native_runtime):
    runtime, _, requests = native_runtime
    async with runtime.capabilities(lambda: None) as capabilities:
        with pytest.raises(Exception, match="target_not_authorized"):
            await harness("C2").run("send", bindings=RunBindings.embedded(capabilities=capabilities))
    assert requests == []


async def test_no_context_does_not_enumerate_workspace_accounts(native_runtime):
    runtime, current, requests = native_runtime
    current[0] = replace(current[0], native_tool_contexts=())
    async with runtime.capabilities(lambda: None) as capabilities:
        assert capabilities == ()
    assert requests == []


async def test_protected_context_rejects_unknown_duplicate_or_incompatible_scope(native_runtime):
    _, current, _ = native_runtime
    context = current[0].native_tool_contexts[0]
    encoded = context.model_dump(mode="json")
    assert parse_native_contexts([encoded]) == (context,)
    with pytest.raises(ValueError, match="duplicate_native_tool_source"):
        parse_native_contexts([encoded, encoded])
    with pytest.raises(ValidationError):
        parse_native_contexts([{**encoded, "kind": "unknown"}])
    with pytest.raises(ValidationError):
        AccountRunContext.model_validate({**encoded, "allowed_actions": ["slack.send_message", "slack.send_message"]})
    with pytest.raises(ValidationError):
        AccountRunContext.model_validate({**encoded, "target_scope": {"chat_ids": ["C1"]}})


async def test_lark_attempt_reuses_token_and_rotation_replaces_scope(
    connectivity_sessions, credential_protector, monkeypatch, external_runtime_factory, execution_authorization
):
    async with transaction(connectivity_sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT_ID)
        account.provider_key = "lark"
        account.provider_config_json = {
            "brand": "feishu",
            "open_api_origin": "https://8.8.8.8",
            "app_id": "app",
            "tenant_key": "tenant",
            "bot_open_id": "bot",
        }
        account.replace_credential('{"app_secret":"first"}', credential_protector)
        context = await bind_account_tools(
            session,
            actor=actor(),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            account_id=ACCOUNT_ID,
            allowed_actions=("lark.send_message",),
            target_scope={"chat_ids": ["chat"]},
        )
    scope = AttemptToolScope(
        replace(actor(), auth_method="internal"),
        ORG_ID,
        WORKSPACE_ID,
        FrozenRunConnectivity(()),
        (context,),
        authorization=await execution_authorization(),
    )
    tokens, calls = [], []

    def send(request):
        if "/auth/" in request.url.path:
            secret = json.loads(request.content)["app_secret"]
            tokens.append(secret)
            return httpx2.Response(200, json={"code": 0, "tenant_access_token": secret, "expire": 7200})
        calls.append(request.headers["authorization"])
        return httpx2.Response(200, json={"code": 0, "data": {"message_id": "message"}})

    policy = EndpointPolicy()
    runtime = external_runtime_factory(
        ConnectorProviderRegistry(()), RemoteTransport(policy), policy, transport=httpx2.MockTransport(send)
    )

    async def read_scope(_attempt, *, accepted=None):
        return scope

    monkeypatch.setattr(runtime, "_scope", read_scope)

    async def model(messages, info):
        if messages[-1].parts[0].part_kind == "tool-return":
            yield "done"
        else:
            yield {
                0: DeltaToolCall(
                    name=info.function_tools[0].name,
                    json_args=json.dumps({"chat_id": "chat", "content": {"kind": "text", "text": "hello"}}),
                    tool_call_id="send",
                )
            }

    agent = HarnessBuilder().build(AgentSpec(), model=FunctionModel(stream_function=model), output_type=str)
    async with runtime.capabilities(lambda: None) as capabilities:
        for _ in range(2):
            await agent.run("send", bindings=RunBindings.embedded(capabilities=capabilities))
        assert tokens == ["first"]
        async with transaction(connectivity_sessions) as session:
            account = await session.get(AccountRecord, ACCOUNT_ID)
            account.replace_credential('{"app_secret":"rotated"}', credential_protector)
        await agent.run("send", bindings=RunBindings.embedded(capabilities=capabilities))
    assert tokens == ["first", "rotated"]
    assert calls == ["Bearer first", "Bearer first", "Bearer rotated"]
