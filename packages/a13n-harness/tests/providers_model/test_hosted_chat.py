"""Exercise the hosted adapters through their native wire serializers."""

import json

import httpx2
import pytest
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from a13n_harness.providers.model.definition import ProviderOperationError
from anyio import create_task_group, sleep
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, ToolCallPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition

MESSAGES = [ModelRequest(parts=[UserPromptPart("Look up the example")])]
PARAMETERS = ModelRequestParameters(
    function_tools=[
        ToolDefinition(
            name="lookup",
            parameters_json_schema={"type": "object", "properties": {"name": {"type": "string"}}},
        )
    ]
)


def definition(kind):
    return next(item for item in BUILT_IN_MODEL_PROVIDERS if item.type == kind)


def completion(request):
    body = json.loads(request.content)
    call = {"id": "call12345", "type": "function", "function": {"name": "lookup", "arguments": '{"name":"test"}'}}
    base = {
        "id": "fixture",
        "object": "chat.completion.chunk" if body.get("stream") else "chat.completion",
        "created": 0,
        "model": body["model"],
    }
    usage = {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9}
    if body.get("stream"):
        chunks = [
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "tool_calls": [{"index": 0, **call}]},
                        "finish_reason": None,
                    }
                ],
            },
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}], "usage": usage},
        ]
        payload = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx2.Response(200, content=payload, headers={"content-type": "text/event-stream"})
    return httpx2.Response(
        200,
        json={
            **base,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": None, "tool_calls": [call]},
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": usage,
        },
    )


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["cerebras", "sambanova", "vercel", "mistral", "xai"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_hosted_chat_preserves_tools_usage_headers_and_caller_client(kind, streaming):
    requests = []
    failed = False

    async def handler(request):
        requests.append(request)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer fixture"
        assert request.headers["x-routing-key"] == "team"
        assert request.headers["x-affinity"] == "request"
        assert json.loads(request.content)["tools"][0]["function"]["name"] == "lookup"
        if failed:
            return httpx2.Response(401, json={"message": "invalid key", "error": {"message": "invalid key"}})
        return completion(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = await definition(kind).build(
            "fixture-model",
            configuration={"base_url": "https://gateway.example" + ("" if kind == "mistral" else "/v1")},
            credential={"api_key": "fixture"},
            http_client=client,
            extra_headers={"x-routing-key": "team", "X-Affinity": "provider"},
        )
        if kind == "xai":
            assert model.system == "xai"
            assert not model.profile["supported_native_tools"]
        settings = {"extra_headers": {"x-affinity": "request"}}

        async def run():
            if streaming:
                async with model.request_stream(MESSAGES, settings, PARAMETERS) as response:
                    async for _ in response:
                        pass
                    return response.get()
            return await model.request(MESSAGES, settings, PARAMETERS)

        async with model:
            response = await run()
            [part] = response.parts
            assert isinstance(part, ToolCallPart)
            assert part.tool_name == "lookup" and part.args_as_dict() == {"name": "test"}
            assert response.usage.total_tokens == 9
            failed = True
            with pytest.raises(ModelHTTPError) as error:
                await run()
            assert error.value.status_code == 401
        assert not client.is_closed
        assert "x-affinity" not in client.headers
    assert len(requests) == 2  # The adapters must not add SDK retries.


@pytest.mark.anyio
async def test_mistral_concurrent_request_headers_do_not_leak_after_failure():
    seen = {}

    async def handler(request):
        name = json.loads(request.content)["messages"][0]["content"]
        await sleep(0)
        seen[name] = request.headers.get("x-affinity")
        if name == "failed":
            return httpx2.Response(401, json={"message": "invalid key"})
        return completion(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = await definition("mistral").build(
            "fixture-model", configuration={}, credential={"api_key": "fixture"}, http_client=client
        )

        async def run(name):
            await model.request(
                [ModelRequest(parts=[UserPromptPart(name)])], {"extra_headers": {"x-affinity": name}}, PARAMETERS
            )

        async with model:
            async with create_task_group() as group:
                group.start_soon(run, "first")
                group.start_soon(run, "second")
            with pytest.raises(ModelHTTPError):
                await run("failed")
            await model.request([ModelRequest(parts=[UserPromptPart("plain")])], None, PARAMETERS)
    assert seen == {"first": "first", "second": "second", "failed": "failed", "plain": None}


@pytest.mark.anyio
async def test_vercel_public_catalog_is_not_an_authentication_probe():
    provider = definition("vercel")
    assert not provider.supports_connection_probe
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: pytest.fail("Public catalog must not be probed"))
    ) as client:
        with pytest.raises(ProviderOperationError, match="Test a saved Model"):
            await provider.probe(provider.bind({}, {"api_key": "fixture"}), http_client=client)
