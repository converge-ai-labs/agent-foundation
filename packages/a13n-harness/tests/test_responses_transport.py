"""Exercise the pinned native Responses transport with a real local socket."""

import json
from contextlib import asynccontextmanager

import httpx2
import pytest
from a13n_harness import HarnessBuilder
from a13n_harness.models.responses import ResponsesModel
from openai import AsyncOpenAI
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.providers.openai import OpenAIProvider
from websockets.asyncio.server import serve

from .test_first_party_events import _collect_extensions, _payloads

pytestmark = pytest.mark.anyio
PARAMETERS = ModelRequestParameters()


def events(response_id="resp-test"):
    response = {
        "id": response_id,
        "created_at": 1,
        "model": "gpt-5",
        "object": "response",
        "output": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }
    return [
        {"type": "response.created", "sequence_number": 0, "response": {**response, "status": "in_progress"}},
        {
            "type": "response.output_item.added",
            "sequence_number": 1,
            "output_index": 0,
            "item": {"type": "message", "id": "msg-test", "role": "assistant", "content": [], "status": "in_progress"},
        },
        {
            "type": "response.content_part.added",
            "sequence_number": 2,
            "output_index": 0,
            "content_index": 0,
            "item_id": "msg-test",
            "part": {"type": "output_text", "text": "", "annotations": []},
        },
        {
            "type": "response.output_text.delta",
            "sequence_number": 3,
            "output_index": 0,
            "content_index": 0,
            "item_id": "msg-test",
            "delta": "OK",
        },
        {"type": "response.completed", "sequence_number": 4, "response": {**response, "status": "completed"}},
    ]


@asynccontextmanager
async def endpoint(*, process_request=None, handler=None):
    sockets, requests, http_requests = [], [], []

    async def respond(socket):
        sockets.append(socket)
        async for message in socket:
            requests.append(json.loads(message))
            for event in events(f"resp-{len(requests)}"):
                await socket.send(json.dumps(event))

    async def http_respond(request):
        http_requests.append(json.loads(request.content))
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join(f"data: {json.dumps(event)}\n\n" for event in events()),
        )

    async with serve(handler or respond, "127.0.0.1", 0, process_request=process_request) as server:
        port = server.sockets[0].getsockname()[1]
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(http_respond)) as http:
            client = AsyncOpenAI(api_key="test-key", base_url=f"http://127.0.0.1:{port}/v1", http_client=http)
            model = ResponsesModel(
                "gpt-5",
                provider=OpenAIProvider(openai_client=client),
                settings={"openai_transport": "websocket", "openai_store": False},
            )
            yield model, sockets, requests, http_requests


async def consume(model, messages, settings=None):
    async with model.request_stream(messages, settings, PARAMETERS) as response:
        async for _ in response:
            pass
        return response.get()


@pytest.mark.parametrize("transport", ["http", "websocket"])
async def test_actual_transport_event_without_instrumentation(transport):
    async with endpoint() as (model, _sockets, requests, http_requests):
        executable = HarnessBuilder(instrumentation=None).build(
            AgentSpec(model_settings={"openai_transport": transport}), model=model, output_type=str
        )
        extensions, terminal = await _collect_extensions(executable, "Say OK")
        assert terminal.result.status == "completed"
        lifecycle = _payloads(extensions, "lifecycle")
        observed = [event for event in lifecycle if event["type"] == "model_request_transport"]
        assert observed == [
            {
                "type": "model_request_transport",
                "request_id": "model-request-1",
                "request_index": 0,
                "transport": transport,
            }
        ]
        assert len(requests) == (transport == "websocket")
        assert len(http_requests) == (transport == "http")


async def test_native_auto_continuation_reuses_socket_with_store_false():
    async with endpoint() as (model, sockets, requests, http_requests):
        messages = [ModelRequest(parts=[UserPromptPart("One")])]
        async with model:
            first = await consume(model, messages)
            messages.extend([first, ModelRequest(parts=[UserPromptPart("Two")])])
            second = await consume(model, messages)
            assert second.provider_details["transport"] == "websocket"
        assert len(sockets) == 1
        assert len(requests) == 2
        assert requests[0]["store"] is False
        assert requests[1]["previous_response_id"] == "resp-1"
        assert requests[1]["input"] == [{"role": "user", "content": "Two"}]
        assert not http_requests


async def test_native_handshake_status_is_preserved():
    from pydantic_ai.exceptions import ModelHTTPError

    def reject(socket, request):
        return socket.respond(503, "Unavailable")

    async with endpoint(process_request=reject) as (model, _sockets, requests, http_requests):
        async with model:
            with pytest.raises(ModelHTTPError) as error:
                await consume(model, [])
        assert error.value.status_code == 503
        assert not requests
        assert not http_requests


async def test_native_connect_failure_uses_http(monkeypatch):
    import openai.lib._websocket as sdk

    async def unavailable(*args, **kwargs):
        raise OSError("Connection unavailable")

    monkeypatch.setattr(sdk, "_WebSocketConnect", unavailable)
    async with endpoint() as (model, _sockets, requests, http_requests):
        async with model:
            with pytest.warns(UserWarning, match="could not be established"):
                result = await consume(model, [])
        assert (result.provider_details or {}).get("transport") != "websocket"
        assert not requests
        assert len(http_requests) == 1


async def test_post_dispatch_disconnect_is_not_replayed():
    requests = []

    async def disconnect(socket):
        requests.append(await socket.recv())
        await socket.close(code=1011, reason="Interrupted")

    async with endpoint(handler=disconnect) as (model, _sockets, _, http_requests):
        from pydantic_ai.exceptions import ModelAPIError

        async with model:
            with pytest.raises(ModelAPIError):
                await consume(model, [])
        assert len(requests) == 1
        assert not http_requests


@pytest.mark.parametrize("limit", [100, 900])
async def test_websocket_response_byte_limit(limit):
    from pydantic_ai.exceptions import ModelAPIError

    async with endpoint() as (model, _sockets, requests, http_requests):
        model.max_response_bytes = limit
        async with model:
            with pytest.raises(ModelAPIError):
                await consume(model, [])
        assert len(requests) == 1
        assert not http_requests


async def test_endpoint_policy_runs_before_connect():
    validated = []

    class Deny:
        async def validate(self, endpoint):
            validated.append(endpoint)
            raise ValueError("Endpoint denied")

    async with endpoint() as (model, sockets, _requests, http_requests):
        model.endpoint_policy = Deny()
        async with model:
            with pytest.raises(ValueError, match="Endpoint denied"):
                await consume(model, [])
        assert validated == [str(model.client.base_url) + "responses"]
        assert not sockets and not http_requests


async def test_redirect_does_not_forward_credentials():
    redirected = []

    async def target(socket):
        redirected.append(socket.request)

    async with serve(target, "127.0.0.1", 0) as other:

        def redirect(socket, request):
            result = socket.respond(302, "Moved")
            result.headers["Location"] = f"ws://127.0.0.1:{other.sockets[0].getsockname()[1]}/responses"
            return result

        async with endpoint(process_request=redirect) as (model, _sockets, _requests, http_requests):
            async with model:
                with pytest.warns(UserWarning, match="could not be established"):
                    await consume(model, [])
            assert not redirected
            assert len(http_requests) == 1


async def test_native_header_mismatch_and_busy_lane_use_http():
    async with endpoint() as (model, _sockets, requests, http_requests):
        async with model:
            async with model.request_stream([], {"extra_headers": {"x-session": "one"}}, PARAMETERS) as first:
                await consume(model, [], {"extra_headers": {"x-session": "one"}})
                async for _ in first:
                    pass
            await consume(model, [], {"extra_headers": {"x-session": "two"}})
        assert len(requests) == 1
        assert len(http_requests) == 2


async def test_native_idle_close_reconnects():
    from anyio import Event

    closed = Event()
    sockets, requests = [], []

    async def respond(socket):
        sockets.append(socket)
        async for message in socket:
            requests.append(json.loads(message))
            for event in events(f"resp-{len(requests)}"):
                await socket.send(json.dumps(event))
            if len(sockets) == 1:
                await socket.close()
                closed.set()

    async with endpoint(handler=respond) as (model, _, _, http_requests):
        async with model:
            first = await consume(model, [])
            await closed.wait()
            second = await consume(model, [first, ModelRequest(parts=[UserPromptPart("Again")])])
        assert second.provider_details["transport"] == "websocket"
        assert len(sockets) == 2
        assert len(requests) == 2
        assert not http_requests


def test_websocket_proxy_and_tls_operator_settings(monkeypatch):
    import ssl

    from a13n_harness.models.responses import _websocket_options

    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", "local.example")
    monkeypatch.setenv("A13N_OUTBOUND_TLS_VERIFY", "false")
    options = _websocket_options("https://remote.example/responses", 1000)
    assert options["proxy"] == "http://proxy.example:8080"
    assert options["ssl"].verify_mode == ssl.CERT_NONE
    assert options["max_size"] == 1000
    assert _websocket_options("https://local.example/responses", None)["proxy"] is None


@pytest.mark.parametrize("settings", [None, {}, {"openai_transport": "http"}])
async def test_codex_uses_http_without_explicit_websocket(monkeypatch, settings):
    from datetime import UTC, datetime, timedelta

    import openai.lib._websocket as sdk
    from a13n_harness.models.codex import CodexRequestModel

    from .test_model_auth import _codex_credentials, _CodexSource

    requests = []

    async def forbidden_socket(*args, **kwargs):
        pytest.fail("Codex must not select WebSocket implicitly")

    async def respond(request):
        requests.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join(f"data: {json.dumps(event)}\n\n" for event in events()),
        )

    monkeypatch.setattr(sdk, "_WebSocketConnect", forbidden_socket)
    source = _CodexSource(_codex_credentials(marker="initial", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        async with CodexRequestModel("gpt-5", credential_source=source, http_client=http) as model:
            response = await consume(model, [], settings)
    assert len(requests) == 1
    assert (response.provider_details or {}).get("transport") != "websocket"


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("transition", ["reuse", "token_rotation", "new_run"])
async def test_codex_websocket_auth_and_turn_state(monkeypatch, refresh, transition):
    from datetime import UTC, datetime, timedelta

    import openai.lib._websocket as sdk
    from a13n_harness.models.codex import CodexRequestModel
    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage
    from websockets.asyncio.client import connect

    from .test_model_auth import _codex_credentials, _CodexSource

    source = _CodexSource(_codex_credentials(marker="initial", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    handshakes, requests, observations, http_requests = [], [], [], []
    reject_http = False

    def handshake(socket, request):
        handshakes.append(dict(request.headers))
        if refresh and len(handshakes) == 1:
            return socket.respond(401, "Rejected")

    def headers(socket, request, response):
        response.headers["x-codex-turn-state"] = "server-turn"
        return response

    async def respond(socket):
        async for message in socket:
            requests.append(json.loads(message))
            for event in events(f"resp-{len(requests)}"):
                await socket.send(json.dumps(event))

    async def observe(headers):
        observations.append(headers)

    async def http_respond(request):
        nonlocal reject_http
        if request.url.host == "auth.openai.com":
            return httpx2.Response(
                200,
                json={"access_token": f"codex-access-refreshed-{len(source.saved)}", "refresh_token": "new-refresh"},
            )
        assert request.url.host == "chatgpt.com"
        http_requests.append(dict(request.headers))
        if reject_http:
            reject_http = False
            return httpx2.Response(401, json={"error": {"message": "Expired"}})
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream", "x-codex-turn-state": "http-turn"},
            content="".join(f"data: {json.dumps(event)}\n\n" for event in events()),
        )

    async with serve(respond, "127.0.0.1", 0, process_request=handshake, process_response=headers) as server:

        async def routed(url, **options):
            assert url == "wss://chatgpt.com/backend-api/codex/responses"
            options.pop("ssl", None)
            options["proxy"] = None
            return await connect(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/responses", **options)

        monkeypatch.setattr(sdk, "_WebSocketConnect", routed)
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(http_respond)) as http:
            model = CodexRequestModel(
                "gpt-5", credential_source=source, http_client=http, thread_id="thread-test", response_headers=observe
            )
            context = RunContext(deps=None, model=model, usage=RunUsage(), run_id="run-test")
            async with model:
                for _ in range(2):
                    async with model.request_stream(
                        [], {"openai_transport": "websocket"}, PARAMETERS, context
                    ) as response:
                        async for _ in response:
                            pass
                        assert response.get().provider_details["transport"] == "websocket"
                if transition == "token_rotation":
                    # Refresh through the real HTTP auth flow while the reusable socket is live.
                    reject_http = True
                    await consume(model, [], {"openai_transport": "http"})
                    assert len(source.saved) == (2 if refresh else 1)
                elif transition == "new_run":
                    context = RunContext(deps=None, model=model, usage=RunUsage(), run_id="another-run")
                if transition != "reuse":
                    for _ in range(2):
                        async with model.request_stream(
                            [], {"openai_transport": "websocket"}, PARAMETERS, context
                        ) as response:
                            async for _ in response:
                                pass
                            assert (response.get().provider_details or {}).get("transport") != "websocket"
                    assert len(http_requests) == (4 if transition == "token_rotation" else 2)
                    if transition == "token_rotation":
                        assert http_requests[-1]["authorization"] == f"Bearer {source.current.access_token}"
                        assert http_requests[-1]["authorization"] != handshakes[-1]["authorization"]
                        assert http_requests[-1]["x-codex-turn-state"] == "server-turn"
                    else:
                        assert "x-codex-turn-state" not in http_requests[0]
                        assert http_requests[1]["x-codex-turn-state"] == "http-turn"
        assert len(handshakes) == (2 if refresh else 1)
        assert len(requests) == 2
        assert handshakes[0]["authorization"] == "Bearer codex-access-initial"
        assert handshakes[-1]["chatgpt-account-id"] == "account-1"
        assert handshakes[-1]["session-id"] == handshakes[-1]["thread-id"]
        assert sum(headers.get("upgrade") == "websocket" for headers in observations) == 1
        assert len(observations) == 1 + len(http_requests)
        if refresh:
            assert handshakes[-1]["authorization"] == "Bearer codex-access-refreshed-0"
            assert len(source.saved) == (2 if transition == "token_rotation" else 1)
