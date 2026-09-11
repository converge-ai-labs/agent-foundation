"""Native MCP exposes content arguments while retaining the admitted target."""

import json
from dataclasses import replace

import httpx2
import pytest
from a13n_service.connectivity.providers.registry import require_native_provider
from a13n_service.connectivity.toolsets import selected_tools
from a13n_service.endpoint_policy import EndpointPolicy
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


async def test_slack_native_reply_hides_target_and_preserves_typed_unknown_outcome():
    requests = []

    def send(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx2.Response(200, json={"ok": True, "channel": "C-bound", "ts": "2.0"})
        raise httpx2.ReadTimeout("response lost after dispatch")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send)) as http:
        actions = require_native_provider("slack").inbound_actions(
            {"channel_id": "C-bound", "root_thread_ts": "1.0", "conversation_kind": "channel"},
            {"reply_mode": "thread"},
            {},
            {"bot_token": "secret"},
            http,
            EndpointPolicy(),
        )
        reply = actions["slack.reply"]
        schema = reply.definition.inputSchema
        assert set(schema["properties"]) == {"text"}
        assert "C-bound" not in json.dumps(schema) and "secret" not in json.dumps(schema)
        selected_tools([item.definition for item in actions.values()], ("slack.reply",))
        with pytest.raises(ValidationError):
            await reply.call({"text": "hello", "channel_id": "C-other"})
        assert not requests
        assert await reply.call({"text": "hello"}) == {"kind": "succeeded"}
        unknown = await reply.call({"text": "second"})
        assert unknown["kind"] == "outcome_unknown"
        assert len(requests) == 2
        assert all(item["channel"] == "C-bound" and item["thread_ts"] == "1.0" for item in requests)


async def test_native_auto_reply_only_adds_placement_choice():
    async with httpx2.AsyncClient() as http:
        actions = require_native_provider("slack").inbound_actions(
            {"channel_id": "C-bound", "root_thread_ts": "1.0", "conversation_kind": "channel"},
            {"reply_mode": "auto"},
            {},
            {"bot_token": "secret"},
            http,
            EndpointPolicy(),
        )
        assert set(actions["slack.reply"].definition.inputSchema["properties"]) == {"text", "placement"}


async def test_lark_inbound_replies_use_distinct_effect_ids_and_reuse_token():
    from .test_lark import _config
    from .test_lark_client import _AllowEndpoint, _token_response

    writes = []
    tokens = []

    def respond(request):
        if request.url.path.endswith("tenant_access_token/internal"):
            tokens.append(request)
            return _token_response()
        writes.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "code": 0,
                "data": {"message_id": f"om_reply_{len(writes)}", "root_id": "om_root", "thread_id": "omt_thread"},
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        actions = require_native_provider("lark").inbound_actions(
            {"chat_id": "oc_chat", "message_id": "om_message", "discussion_id": "omt_thread", "chat_type": "group"},
            {"reply_mode": "thread"},
            _config(),
            {"app_secret": "private"},
            http,
            _AllowEndpoint(),
        )
        for text in ("first", "second"):
            assert await actions["lark.reply"].call({"content": {"kind": "text", "text": text}}) == {
                "kind": "succeeded"
            }
    assert len(tokens) == 1
    assert len(writes) == 2 and writes[0]["uuid"] != writes[1]["uuid"]


@pytest.mark.parametrize("entry", ["inbound", "account"])
@pytest.mark.parametrize("lost_response", [False, True])
async def test_native_runtime_observes_unknown_without_repeating_effect(
    connectivity_sessions, credential_protector, entry, lost_response, execution_authorization
):
    from a13n_harness import AgentSpec, HarnessBuilder, HarnessInstrumentation, HarnessTraceContent
    from a13n_service.connectivity.accounts.models import AccountRecord
    from a13n_service.connectivity.execution import AttemptToolScope
    from a13n_service.connectivity.native import native_capability
    from a13n_service.connectivity.native_context import AccountRunContext, InboundRunContext
    from a13n_service.connectivity.providers.slack.adapter import CONTEXT_VERSION
    from a13n_service.connectivity.selection_resolution import FrozenRunConnectivity
    from a13n_service.storage import transaction
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import StatusCode
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from .conftest import ACCOUNT_ID, ORG_ID, WORKSPACE_ID, actor

    async with transaction(connectivity_sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT_ID)
        account.provider_key = "slack"
        account.replace_credential('{"bot_token":"private"}', credential_protector)
    principal = actor().principal
    if entry == "inbound":
        context = InboundRunContext(
            binding_id="binding-test",
            account_id=ACCOUNT_ID,
            execution_principal_ref=principal,
            provider_key="slack",
            provider_context_version=CONTEXT_VERSION,
            provider_context={"channel_id": "C1", "root_thread_ts": "1.0", "conversation_kind": "channel"},
            action_policy={"reply_mode": "thread"},
            allowed_actions=("slack.reply",),
        )
        arguments = {"text": "hello"}
    else:
        context = AccountRunContext(
            account_id=ACCOUNT_ID,
            execution_principal_ref=principal,
            provider_key="slack",
            target_scope={"channel_ids": ["C1"]},
            allowed_actions=("slack.send_message",),
        )
        arguments = {"channel_id": "C1", "text": "hello"}
    scope = AttemptToolScope(
        replace(actor(), auth_method="internal"),
        ORG_ID,
        WORKSPACE_ID,
        FrozenRunConnectivity((), ()),
        (context,),
        authorization=await execution_authorization(),
    )
    requests = []

    async def guard():
        pass

    def send(request):
        requests.append(request)
        if lost_response:
            raise httpx2.ReadTimeout("response lost after dispatch")
        return httpx2.Response(200, json={"ok": True, "channel": "C1", "ts": "2.0"})

    step = 0

    async def model(messages, info):
        nonlocal step
        step += 1
        if step == 1:
            yield {
                0: DeltaToolCall(
                    name=info.function_tools[0].name, json_args=json.dumps(arguments), tool_call_id="native"
                )
            }
        else:
            yield "done"

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send)) as http:
        capability = await native_capability(
            connectivity_sessions, credential_protector, scope, context, guard, EndpointPolicy(), http
        )
        executable = HarnessBuilder(
            instrumentation=HarnessInstrumentation(tracer_provider=provider, trace_content=HarnessTraceContent.NONE)
        ).build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=[capability])
        result = await executable.run("send")
    assert result.output_or_raise() == "done" and len(requests) == 1
    tool = next(s for s in exporter.get_finished_spans() if s.attributes.get("gen_ai.operation.name") == "execute_tool")
    assert tool.attributes["a13n.tool.result.status"] == ("outcome_unknown" if lost_response else "returned")
    assert tool.status.status_code is StatusCode.UNSET
    assert "private" not in repr(dict(tool.attributes))
    assert "response lost" not in repr(dict(tool.attributes))
    assert ("outcome_unknown" in repr(result.state)) == lost_response
