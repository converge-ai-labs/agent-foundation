"""Native nested dispatch retains originals without exposing App-only data to the model."""

import json
from dataclasses import dataclass

import httpx2
import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.capabilities import CodeActCapability, ToolSearchCapability, ToolSearchGroup
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.tools.mcp import mcp_capability, mcp_client
from a13n_service.providers.tools.mcp_apps import AppClient, AppMCP, AppToolset
from a13n_service.runs.mcp_apps.service import _resource
from fastmcp import FastMCP
from fastmcp.client.transports import FastMCPTransport
from fastmcp.tools import ToolResult
from mcp.types import BlobResourceContents, ReadResourceResult, TextResourceContents
from pydantic_ai.capabilities import WrapperCapability
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .http_support import serve_http

pytestmark = pytest.mark.anyio
URI = "ui://counter/app.html"
MIME = "text/html;profile=mcp-app"


@pytest.mark.parametrize("codeact", [False, True])
@pytest.mark.parametrize("capture_fails", [False, True])
@pytest.mark.parametrize("tool_error", [False, True])
async def test_nested_calls_keep_outer_identity_and_capture_failure_does_not_retry(codeact, capture_fails, tool_error):
    server = FastMCP("Nested Apps")
    count = 0

    @server.tool(meta={"ui": {"resourceUri": URI}})
    def counter() -> ToolResult:
        nonlocal count
        count += 1
        return ToolResult(
            content="counted",
            structured_content={"count": count},
            meta={"private": "app-only-data"},
            is_error=tool_error,
        )

    @dataclass
    class Eligible(WrapperCapability):
        def get_toolset(self):
            return CodeActPolicyToolset(self.wrapped.get_toolset(), CodeActToolPolicy(tools={"counter": True}))

    captured = []

    def capture(context, call_id, tool, app):
        captured.append((context, call_id, tool, app))
        if capture_fails:
            raise ValueError("Cannot retain presentation")

    async def check(dispatch):
        assert dispatch.connection_id == "con_apps"

    async def model(messages, info):
        returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if returned:
            assert "app-only-data" not in str(returned)
            yield "done"
        else:
            args = (
                {"code": "[await call_tool(group='apps', tool='counter', arguments={}) for _ in range(2)]"}
                if codeact
                else {"group": "apps", "tool": "counter", "arguments": {}}
            )
            yield {
                0: DeltaToolCall(
                    name="run_code" if codeact else "call_tool", json_args=json.dumps(args), tool_call_id="outer"
                )
            }

    async with serve_http(server.http_app(path="/mcp")) as url:
        client = mcp_client(url + "/mcp", "con_apps", httpx2.AsyncClient(), timeout=5, apps=True)
        async with client:

            async def acquire():
                return client

            capability = mcp_capability("con_apps", acquire, tools=None, check=check, capture=capture)
            features = [ToolSearchCapability(groups={"apps": ToolSearchGroup(Eligible(capability), "Apps")})]
            if codeact:
                features.append(CodeActCapability())
            executable = HarnessBuilder().build(
                AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=features,
            )
            result = await executable.run("show", bindings=RunBindings.embedded())
            assert result.output_or_raise() == "done"
    assert count == len(captured) == (2 if codeact and not tool_error else 1)
    assert all(call_id == "outer" and context.run_id == result.run_id for context, call_id, _, _ in captured)
    assert all(app["result"]["_meta"]["private"] == "app-only-data" for _, _, _, app in captured)
    assert all(app["result"]["isError"] == tool_error for _, _, _, app in captured)


@pytest.mark.parametrize("hook", ["inject", "redact", "replace"])
async def test_native_app_capture_respects_hooks_without_saving_injected_arguments(hook):
    server = FastMCP("App hooks")
    dispatched = []

    @server.tool(meta={"ui": {"resourceUri": URI}})
    def show(value: int, token: str = "") -> ToolResult:
        dispatched.append((value, token))
        return ToolResult(content="shown", structured_content={"value": value}, meta={"private": "app-only-data"})

    async def process(ctx, call, name, arguments):
        if hook == "replace":
            return {"redacted": True}
        arguments["token"] = "injected-secret"
        result = await call(name, arguments)
        return {"redacted": True} if hook == "redact" else result

    captured = []
    toolset = AppToolset(AppClient(FastMCPTransport(server)), id="trusted", process_tool_call=process)
    toolset.capture = lambda context, call_id, tool, app: captured.append((context, call_id, tool, app))

    async def model(messages, info):
        returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if returned:
            assert returned[-1].content == ({"value": 7} if hook == "inject" else {"redacted": True})
            assert "mcp_app" not in (returned[-1].metadata or {})
            yield "done"
        else:
            yield {0: DeltaToolCall(name="show", json_args='{"value":7}', tool_call_id="show-call")}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(AppMCP(id="trusted", local=toolset),),
    )
    result = await executable.run("show", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert dispatched == ([] if hook == "replace" else [(7, "injected-secret")])
    assert len(captured) == (1 if hook == "inject" else 0)
    if captured:
        context, call_id, tool, app = captured[0]
        assert (context.run_id, call_id, tool.name) == (result.run_id, "show-call", "show")
        assert app["tool_input"] == {"value": 7}
        assert app["result"]["_meta"]["private"] == "app-only-data"


@pytest.mark.parametrize("kind", ["text", "blob", "wrong_uri", "wrong_mime", "duplicate"])
async def test_resource_requires_exact_uri_and_mime_and_preserves_metadata(kind):
    metadata = {"ui": {"csp": {"resourceDomains": ["https://assets.test"]}}}
    value = TextResourceContents(uri=URI, mime_type=MIME, text="<p>Hello</p>", meta=metadata)
    if kind == "blob":
        value = BlobResourceContents(uri=URI, mime_type=MIME, blob="PHA+SGVsbG88L3A+", meta=metadata)
    elif kind == "wrong_uri":
        value = value.model_copy(update={"uri": "ui://another/app.html"})
    elif kind == "wrong_mime":
        value = value.model_copy(update={"mime_type": "text/html"})

    class Toolset:
        async def read_resource_for_app(self, uri):
            assert uri == URI
            return ReadResourceResult(contents=[value, value] if kind == "duplicate" else [value]).model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )

    if kind in {"text", "blob"}:
        resource = await _resource(Toolset(), URI)
        assert resource.html == "<p>Hello</p>" and resource.metadata == metadata
    else:
        with pytest.raises(ServiceError, match="exactly one"):
            await _resource(Toolset(), URI)
