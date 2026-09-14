"""Check scripted peers against their real consumer contracts without service processes."""

import asyncio
import json
import ssl
from contextlib import suppress

import httpx2
import pytest
from fastapi import FastAPI
from jsonschema import Draft202012Validator

from ..harness_integration.fixture_connectivity import TOOL_OUTPUT_SCHEMA, TOOLKIT_VERSION, connectivity_router
from ..infrastructure.management_packages import skill_zip
from ..infrastructure.management_support import client_tool, has_tool
from ..infrastructure.round_two_lab import private_json
from ..infrastructure.round_two_resources import agent_config
from .test_round_two_support import fixture_http


def chunks(response):
    assert response.status_code == 200
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ") and "[DONE]" not in line
    ]


@pytest.mark.anyio
async def test_owned_tls_peer_is_trusted_by_lab_environment_only(tmp_path, monkeypatch):
    from ..infrastructure.fixture_peer import create_certificate

    config = create_certificate(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(config["peer_certificate"], config["peer_key"])

    async def respond(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
            await writer.drain()
        finally:
            writer.close()
            with suppress(ConnectionResetError):
                await writer.wait_closed()

    async with await asyncio.start_server(respond, "127.0.0.1", 0, ssl=context) as server:
        origin = f"https://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        # Default trust rejects the test CA; production httpx2 inherits only the lab's bundle.
        async with httpx2.AsyncClient(trust_env=False) as untrusted:
            with pytest.raises(httpx2.ConnectError):
                await untrusted.get(origin)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("SSL_CERT_FILE", config["peer_ca_bundle"])
        async with httpx2.AsyncClient() as trusted:
            assert (await trusted.get(origin)).text == "OK"
    assert (tmp_path / "peer.key").stat().st_mode & 0o777 == 0o600


@pytest.mark.anyio
@pytest.mark.parametrize("advertised_name", ["live_echo", "mcp_5534dcadf444ea40_live_echo_6dd03da008c5745e"])
async def test_script_uses_observed_results_and_resets_for_new_continuation(tmp_path, advertised_name):
    first = {"case_id": "a" * 32, "scenario": "management", "token": "b" * 32}
    second = {**first, "case_id": "c" * 32}
    async with fixture_http(tmp_path) as client:
        messages = [{"role": "user", "content": "LIVE_TEST " + json.dumps(first)}]
        for case in (first, second):
            await client.put("/__live__/cases/" + case["case_id"], json=case)
            private_json(
                tmp_path / case["case_id"] / "plan.json",
                {"steps": [{"tool": "live_echo", "arguments": {"value": "argument"}}]},
            )
            body = {"stream": True, "messages": messages, "tools": [{"function": {"name": advertised_name}}]}
            assert has_tool({"body": body}, "live_echo")
            assert not has_tool({"body": body}, "live_forbidden")
            response = await client.post("/__live__/model/v1/chat/completions", json=body)
            calls = [
                item["choices"][0]["delta"]["tool_calls"]
                for item in chunks(response)
                if item["choices"] and "tool_calls" in item["choices"][0]["delta"]
            ]
            assert len(calls) == 1 and calls[0][0]["function"]["name"] == advertised_name
            messages.append({"role": "tool", "tool_call_id": calls[0][0]["id"], "content": "actual-peer-result"})
            response = await client.post("/__live__/model/v1/chat/completions", json=body)
            text = "".join(
                item["choices"][0]["delta"].get("content", "") for item in chunks(response) if item["choices"]
            )
            assert "actual-peer-result" in text and case["token"] not in text
            messages.append({"role": "user", "content": "LIVE_TEST " + json.dumps(second)})


def test_managed_upload_and_agent_payloads_match_current_contracts():
    from a13n_service.agents.domain import AgentConfig
    from a13n_service.skills.package import normalize_skill_zip

    package = normalize_skill_zip(skill_zip("live-proof", "DOCUMENT", "ATTACHMENT"))
    assert package.manifest.skill_name == "live-proof"
    assert {file.path: file.content for file in package.files}["references/proof.txt"] == b"ATTACHMENT"
    for definition in (
        agent_config(client_tools=[client_tool()]),
        agent_config(skills=[{"skill_key": "live-proof", "version": 1}]),
        agent_config(asset_publication={"enabled": True}),
        agent_config(
            output_spec={
                "name": "proof",
                "schema": {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]},
            }
        ),
        agent_config(connection_tools=[{"connection_id": "mcpc_" + "a" * 24, "tools": ["live_echo"]}]),
    ):
        AgentConfig.model_validate(definition)


@pytest.mark.anyio
async def test_composio_fixture_is_usable_by_production_adapter(tmp_path):
    from a13n_service.connectivity.connectors.contracts import ConnectionBinding, SetupContext
    from a13n_service.connectivity.connectors.http import ConnectorHttpClient
    from a13n_service.connectivity.connectors.providers.composio.runtime import ComposioProvider
    from a13n_service.connectivity.connectors.providers.configuration import ApiKeyCredentials

    from ..harness_integration.connector_host import PeerEndpoint

    origin = "https://127.0.0.1:9443"
    app = FastAPI()
    app.include_router(connectivity_router(tmp_path, {"token": "fixture-token", "control_url": origin}))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)) as http:
        policy = PeerEndpoint(origin)
        with pytest.raises(ValueError, match="unexpected upstream"):
            await policy.validate("https://unexpected.example")
        provider = ComposioProvider(
            ConnectorHttpClient(http, policy, response_max_bytes=1024 * 1024),
            ApiKeyCredentials(api_key="fixture-token"),
        )
        assert await provider.test() == ("account_read",)
        assert (await provider.discover_connectors())[0].key == "live"
        context = SetupContext(
            attempt_id="attempt",
            generation=1,
            connector_key="live",
            external_user_correlation="owner",
            callback_url=origin + "/callback",
        )
        started = await provider.start_setup(
            setup={"auth_config_id": "live-auth", "toolkit_version": TOOLKIT_VERSION}, context=context
        )
        inspection = await provider.inspect_setup(setup_ref=started.setup_ref, context=context)
        assert inspection.status == "pending"
        inspection = await provider.complete_setup(
            session_uri="live-session", context=context, expected_external_ref=started.external_ref
        )
        assert inspection.status == "ready"
        connected = provider.connect(
            ConnectionBinding(
                external_ref=inspection.external_ref, connector_key="live", external_user_correlation="owner"
            )
        )
        catalog = await connected.discover_tools(cursor=None)
        assert {tool.key for tool in catalog.items} == {"live_echo", "live_forbidden"}
        assert all(tool.output_schema == TOOL_OUTPUT_SCHEMA for tool in catalog.items)
        fenced = []

        async def before_dispatch():
            fenced.append(True)

        result = await connected.execute_tool(
            tool_key="live_echo",
            provider_version=catalog.provider_version,
            arguments={"value": "proof"},
            request_id="request",
            before_dispatch=before_dispatch,
        )
        assert result.result == {"successful": True, "data": {"proof": "REMOTE:proof"}}
        assert fenced == [True]
        Draft202012Validator(TOOL_OUTPUT_SCHEMA).validate(result.result)
        await connected.revoke(operation_id="revoke")
        assert (await connected.inspect()).status == "action_required"


@pytest.mark.anyio
async def test_mcp_fixture_uses_valid_wire_results_and_checks_credentials(tmp_path):
    from mcp.types import CallToolResult, InitializeResult, ListToolsResult

    app = FastAPI()
    app.include_router(connectivity_router(tmp_path, {"token": "fixture-token", "control_url": "http://fixture"}))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://fixture") as http:
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        assert (await http.post("/__live__/mcp", json=body)).status_code == 401
        http.headers["Authorization"] = "Bearer fixture-token"
        initialized = (await http.post("/__live__/mcp", json=body)).json()["result"]
        assert InitializeResult.model_validate(initialized).protocolVersion == "2025-11-25"
        listed = (await http.post("/__live__/mcp", json={**body, "method": "tools/list"})).json()["result"]
        assert len(ListToolsResult.model_validate(listed).tools) == 2
        called = (
            await http.post(
                "/__live__/mcp",
                json={**body, "method": "tools/call", "params": {"name": "live_echo", "arguments": {"value": "proof"}}},
            )
        ).json()["result"]
        assert CallToolResult.model_validate(called).content[0].text == "REMOTE:proof"


@pytest.mark.anyio
async def test_telemetry_fixture_decodes_real_protobuf_and_records_rejections(tmp_path):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    from ..observability.fixture_telemetry import telemetry_router
    from ..observability.test_31_observability import exported_spans

    root = tmp_path / "workspace"
    root.mkdir()
    app = FastAPI()

    async def authenticated():
        return None

    app.include_router(telemetry_router(root, authenticated))
    message = ExportTraceServiceRequest()
    span = message.resource_spans.add().scope_spans.add().spans.add()
    span.name, span.trace_id, span.span_id = "proof", b"t" * 16, b"s" * 8
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://fixture") as http:
        assert (await http.post("/__live__/otlp/v1/traces", content=message.SerializeToString())).status_code == 200
        (root / "telemetry_fail").touch()
        assert (await http.post("/__live__/otlp/v1/traces", content=message.SerializeToString())).status_code == 503
        assert len(exported_spans(tmp_path)) == 1 and exported_spans(tmp_path)[0]["name"] == "proof"
