"""Exercise realtime execution with a Service-composed Agent and a persisted Thread checkpoint."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from importlib import import_module
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness import HarnessEvent, RunBindings, RunConfiguration
from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_harness.providers.model.azure_openai import DEFINITION as AZURE
from a13n_harness.providers.model.google_gemini import DEFINITION as GOOGLE
from a13n_harness.providers.model.openai import DEFINITION as OPENAI
from a13n_harness.providers.model.xai import DEFINITION as XAI
from a13n_service.infra.crypto import KeyRing, SecretLocation
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.registry import Registry
from a13n_service.resources.models.realtime import open_realtime_model
from a13n_service.resources.models.schemas import ModelConfig
from a13n_service.resources.models.service import ResolvedModel
from a13n_service.resources.providers.service import ResolvedProvider
from a13n_service.runs import agent, checkpoints, realtime_cleanup
from a13n_service.runs.seal import LeaseExpirer
from a13n_service.runs.tables import AttemptRow, RealtimeCallRow, RunRow
from a13n_service.settings import Providers
from pydantic import SecretStr
from pydantic_ai import ApprovalRequired, ModelRetry, RunContext, Tool
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import BinaryAudio, ModelMessage, RealtimeSessionReconnectEvent, RealtimeTurnCompleteEvent
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.realtime import RealtimeModelSettings, WebRTCAnswer, WebRTCSession
from pydantic_ai.realtime.azure import AzureRealtimeModel
from pydantic_ai.realtime.codec import (
    AudioDelta,
    OutputTranscript,
    RealtimeCodecEvent,
    RealtimeConnection,
    RealtimeInput,
    ResponseDone,
    ToolCall,
    ToolResult,
    TruncateOutput,
)
from pydantic_ai.realtime.google import GoogleRealtimeModel
from pydantic_ai.realtime.lifecycle import InferredLifecycle, LifecycleEvent, RealtimeFrame
from pydantic_ai.realtime.openai import OpenAIRealtimeModel
from pydantic_ai.realtime.openai_live import OpenAILiveModel
from pydantic_ai.realtime.xai import XaiRealtimeModel
from sqlalchemy import JSON, func, select, update
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError, InvalidStatus

from .http_support import serve_http

pytestmark = pytest.mark.anyio

OPENAI_AUDIO_OFFER = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"


@pytest.fixture
async def realtime_agent(service, runs_kit):
    return await create_voice_agent(service, runs_kit)


async def create_voice_agent(service, runs_kit, model_api="openai.realtime"):
    provider_type, model_name = {
        "openai.realtime": ("openai", "gpt-realtime-2.1"),
        "openai.live": ("openai", "gpt-live-1"),
        "azure.realtime": ("azure_openai", "azure-deployment"),
        "google.realtime": ("google_gemini", "gemini-3.1-flash-live-preview"),
        "xai.realtime": ("xai", "grok-voice-latest"),
    }[model_api]
    config = {"resource_endpoint": "https://resource.openai.azure.com"} if model_api == "azure.realtime" else {}
    provider = await service.client.post(
        f"{service.api}/model-providers",
        json={"type": provider_type, "name": "Realtime", "config": config, "credential": {"api_key": "test-key"}},
    )
    assert provider.status_code == 201, provider.text
    model = await service.client.post(
        f"{service.api}/models",
        json={
            "provider_id": provider.json()["id"],
            "key": "voice",
            "name": "Voice",
            "config": {"model_name": model_name, "model_api": model_api},
        },
    )
    assert model.status_code == 201, model.text
    return await runs_kit.add_agent(service, "voice", model.json()["key"])


async def test_realtime_subagent_model_selection(service, runs_kit, realtime_agent, scripted_model):
    text = await runs_kit.create_model(service, scripted_model)
    for policy, field in [
        ({"mode": mode, "named": {"child": {"agent_id": realtime_agent["id"]}}}, "subagents.named.child")
        for mode in ("inline", "async")
    ] + [
        ({"dynamic": {"enabled": True}}, "subagents.dynamic.default_model"),
        (
            {"dynamic": {"enabled": True, "allowed_models": [text, "voice"], "default_model": text}},
            "subagents.dynamic.allowed_models",
        ),
    ]:
        response = await service.client.post(
            f"{service.api}/agents",
            json={"name": "Parent", "config": {"model": "voice", "subagents": {"mode": "async", **policy}}},
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"]["details"]["field"] == field
    await runs_kit.add_agent(
        service,
        "Parent",
        "voice",
        subagents={"mode": "async", "dynamic": {"enabled": True, "allowed_models": [text], "default_model": text}},
    )


async def test_http_submission_requires_a_realtime_connection(service, runs_kit, realtime_agent):
    response = await service.client.post(
        f"{service.api}/threads",
        json=runs_kit.message(realtime_agent, "Hello"),
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["details"]["reason"] == "This model requires a realtime conversation"
    assert (await service.client.get(f"{service.api}/threads")).json()["items"] == []


@pytest.mark.parametrize("strict", [False, True])
async def test_realtime_budget_restriction_is_reported_before_start(service, realtime_agent, monkeypatch, strict):
    runtime = replace(service.runtime, admission=Mock(requires_per_dispatch_reservation=strict))
    monkeypatch.setattr(service.app.state, "runtime", runtime)
    response = await service.client.get(f"{service.api}/realtime", params={"agent_id": realtime_agent["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["can_start"] is (not strict)
    if strict:
        assert "per-call budget reservations" in response.json()["reason"]
    ticket = await service.client.post(f"{service.api}/realtime/sessions", json={"agent_id": realtime_agent["id"]})
    assert ticket.status_code == (503 if strict else 201)


async def test_failed_plan_finishes_an_accepted_realtime_connection(settings, serve, runs_kit, monkeypatch):
    execution = import_module("a13n_service.runs.execute")
    monkeypatch.setattr(execution, "_plan", AsyncMock(side_effect=ServiceError("forbidden", "Execution denied")))
    async with serve(role="all", settings=settings) as service:
        agent_view = await create_voice_agent(service, runs_kit)
        bearer = await runs_kit.bearer(service)
        response = await service.client.post(
            f"{service.api}/realtime/sessions",
            headers=bearer,
            json={
                "agent_id": agent_view["id"],
            },
        )
        assert response.status_code == 201, response.text
        ticket = response.json()
        async with serve_http(service.app, lifespan="off") as url, asyncio.timeout(10):
            socket_url = url.replace("http://", "ws://", 1) + ticket["websocket_path"]
            async with connect(socket_url, subprotocols=[ticket["subprotocol"], ticket["ticket"]]) as socket:
                accepted = json.loads(await socket.recv())
                assert accepted["type"] == "accepted"
                assert json.loads(await socket.recv()) == {
                    "type": "error",
                    "code": "authority_revoked",
                    "message": "The realtime run failed",
                }
                assert json.loads(await socket.recv()) == {
                    "type": "sealed",
                    "status": "failed",
                    "run_id": ticket["run_id"],
                    "reason": "authority_revoked",
                }
        assert (await runs_kit.get_run(service, accepted["run_id"]))["status"] == "failed"


@pytest.mark.parametrize(
    ("provider_type", "api", "name", "adapter"),
    [
        ("openai", "openai.realtime", "gpt-realtime-2.1", OpenAIRealtimeModel),
        ("openai", "openai.live", "gpt-live-1", OpenAILiveModel),
        ("azure_openai", "azure.realtime", "azure-deployment", AzureRealtimeModel),
        ("google_gemini", "google.realtime", "gemini-3.1-flash-live-preview", GoogleRealtimeModel),
        ("xai", "xai.realtime", "grok-voice-latest", XaiRealtimeModel),
    ],
)
async def test_primary_model_selects_its_realtime_adapter(provider_type, api, name, adapter, monkeypatch):
    location = SecretLocation("org_test", "model_providers", "credential", "mp_test")
    keys = KeyRing(active_key_id="test", keys={"test": SecretStr(base64.b64encode(bytes(32)).decode())})
    provider = ResolvedProvider(
        id="mp_test",
        version=1,
        type=provider_type,
        config={"resource_endpoint": "https://resource.openai.azure.com"} if api == "azure.realtime" else {},
        enabled=True,
        credential=keys.protect(b'{"api_key":"test-key"}', location),
        extra_headers={},
        location=location,
    )
    model = ResolvedModel(
        id="mdl_test",
        key="primary",
        version=1,
        config=ModelConfig(model_name=name, model_api=api),
        pricing=None,
        provider=provider,
    )
    registry = Registry.of((OPENAI, XAI, AZURE, GOOGLE))
    options = {"registry": registry, "keys": keys, "settings": Providers()}
    async with open_realtime_model(model, policy=EndpointPolicy(), **options) as native:
        assert type(native) is adapter and native.model_name == name
        assert native.profile["audio_input_sample_rate"] == (16000 if api == "google.realtime" else 24000)
        assert native.profile["audio_output_sample_rate"] == 24000
        if api in {"azure.realtime", "google.realtime"}:
            assert native.profile["supports_image_input"]
        if isinstance(native, AzureRealtimeModel):
            assert native.profile["azure_realtime_apis"] == frozenset(("azure_openai",))
        if isinstance(native, GoogleRealtimeModel):
            socket = AsyncMock()
            socket.recv.return_value = b'{"setupComplete":{"sessionId":"session"}}'

            @asynccontextmanager
            async def mock_connect(url, *, additional_headers, **options):
                assert url == (
                    "wss://generativelanguage.googleapis.com/ws/"
                    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
                )
                assert additional_headers["x-goog-api-key"] == "test-key"
                yield socket

            monkeypatch.setattr("google.genai.live.ws_connect", mock_connect)
            async with native.client.aio.live.connect(model=name) as session:
                assert session.session_id == "session"
            socket.send.assert_awaited_once()

    restricted = EndpointPolicy().for_run(RunConfiguration(allowed_hosts=frozenset()))
    with pytest.raises(ServiceError, match="outbound policy"):
        async with open_realtime_model(model, policy=restricted, **options):
            pytest.fail("a disabled destination was accepted")

    custom = replace(
        model, provider=replace(provider, config={**provider.config, "base_url": "https://gateway.example/v1"})
    )
    with pytest.raises(ServiceError, match="official API endpoint"):
        async with open_realtime_model(custom, policy=EndpointPolicy(), **options):
            pytest.fail("a custom realtime destination was accepted")

    assert provider.credential is not None
    for header in ("x-gateway", "Authorization", "Sec-WebSocket-Protocol"):
        with_headers = replace(model, provider=replace(provider, extra_headers={header: provider.credential}))
        with pytest.raises(ServiceError, match="without custom headers"):
            async with open_realtime_model(with_headers, policy=EndpointPolicy(), **options):
                pytest.fail("an unsupported realtime header was accepted")

    attempts = []
    validate = EndpointPolicy.validate

    async def deny_upgrade(policy, endpoint):
        attempts.append(endpoint)
        if len(attempts) == 2:
            raise EndpointPolicyError("WebSocket blocked")
        return await validate(policy, endpoint)

    monkeypatch.setattr(EndpointPolicy, "validate", deny_upgrade)
    with pytest.raises(ServiceError, match="outbound policy"):
        async with open_realtime_model(model, policy=EndpointPolicy(), **options):
            pytest.fail("a denied WebSocket destination was accepted")
    assert len(attempts) == 2 and attempts[1].startswith(attempts[0].rstrip("/") + "/")


class ScriptedConnection(RealtimeConnection):
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[RealtimeCodecEvent] = asyncio.Queue()
        self.outgoing: asyncio.Queue[RealtimeInput] = asyncio.Queue()
        self.lifecycle = InferredLifecycle()

    async def send(self, content: RealtimeInput) -> list[LifecycleEvent]:
        with self.lifecycle.sending(content):
            await self.outgoing.put(content)
        return self.lifecycle.take_pending()

    def frames(self) -> AsyncIterator[RealtimeFrame]:
        return self.lifecycle.frames(self._events())

    async def _events(self) -> AsyncIterator[RealtimeCodecEvent]:
        while True:
            yield await self.incoming.get()


class ScriptedRealtimeProvider:
    def __init__(self) -> None:
        self.connection = ScriptedConnection()
        self.messages: Sequence[ModelMessage] = ()
        self.settings: RealtimeModelSettings = {}
        self.tools: set[str] = set()

    @asynccontextmanager
    async def connect(
        self,
        *,
        messages: Sequence[ModelMessage],
        model_settings: RealtimeModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> AsyncIterator[RealtimeConnection]:
        self.settings = model_settings or {}
        self.messages = messages
        self.tools = {tool.name for tool in model_request_parameters.function_tools}
        yield self.connection


class ScriptedWebRTCProvider(ScriptedRealtimeProvider):
    session = WebRTCSession(provider_name="openai", session_id="provider-call")

    def __init__(self) -> None:
        super().__init__()
        self.hangups = 0
        self.offer_tools: set[str] = set()
        self.attach_error: ServiceError | None = None

    async def answer(self, offer, *, tools, **kwargs):
        assert offer == OPENAI_AUDIO_OFFER
        self.offer_tools = {tool.name for tool in tools}
        return WebRTCAnswer(sdp="answer-sdp", session=self.session)

    @asynccontextmanager
    async def attach(self, session, **kwargs):
        assert session is self.session
        if self.attach_error is not None:
            raise self.attach_error
        async with self.connect(**kwargs) as connection:
            yield connection

    async def hang_up(self, session):
        assert session is self.session
        self.hangups += 1


@pytest.mark.parametrize(
    ("api", "transport", "adapter", "split"),
    [
        ("openai.realtime", "websocket", OpenAIRealtimeModel, False),
        ("openai.realtime", "webrtc", OpenAIRealtimeModel, False),
        ("openai.live", "webrtc", OpenAILiveModel, True),
        ("openai.live", "websocket", OpenAILiveModel, False),
        ("azure.realtime", "webrtc", AzureRealtimeModel, False),
        ("google.realtime", "websocket", GoogleRealtimeModel, False),
        ("xai.realtime", "websocket", XaiRealtimeModel, True),
    ],
)
async def test_app_opens_realtime_over_http_and_websocket(
    settings, serve, service_app, runs_kit, monkeypatch, api, transport, adapter, split
):
    audio_enabled = (api, transport) != ("openai.live", "websocket")
    input_hz = 16000 if api == "google.realtime" else 24000
    interruption = api not in {"openai.live", "google.realtime"}
    settings = settings.model_copy(
        update={
            "control": type(settings.control).model_validate(
                {
                    **settings.control.model_dump(),
                    "realtime_ice_servers": [
                        {"urls": ["turn:relay.example.org:3478"], "username": "voice", "credential": "fake-turn"}
                    ],
                }
            )
        }
    )
    provider = ScriptedWebRTCProvider()
    monkeypatch.setattr(adapter, "connect", provider.connect)
    calls = []
    if transport == "webrtc":
        monkeypatch.setattr(adapter, "answer_webrtc_offer", provider.answer)
        monkeypatch.setattr(adapter, "connect_webrtc", provider.attach)
        monkeypatch.setattr(adapter, "hang_up", provider.hang_up)
        build = agent.build

        def validate_order(ctx: RunContext) -> None:
            if ctx.tool_call_approved and ctx.tool_call_id == "call_revalidate":
                raise ModelRetry("Order changed while waiting for approval")

        def lookup_order(ctx: RunContext) -> str:
            if not ctx.tool_call_approved:
                raise ApprovalRequired()
            calls.append("lookup_order")
            return "Order shipped"

        def with_tool(root, *, capabilities, **kwargs):
            return build(
                root,
                capabilities=lambda node: [
                    *capabilities(node),
                    # Cover both a declared approval and a tool that asks from its body.
                    Capability(tools=[Tool(lookup_order, requires_approval=not split, args_validator=validate_order)]),
                ],
                **kwargs,
            )

        monkeypatch.setattr(agent, "build", with_tool)
    async with AsyncExitStack() as stack:
        service = await stack.enter_async_context(serve(role="control" if split else "all", settings=settings))
        ingress = service.app
        if split:
            ingress = service_app(role="control", settings=settings)
            await stack.enter_async_context(ingress.router.lifespan_context(ingress))
            # Both independent workers watch the same PostgreSQL queue and use separate Redis clients.
            for _ in range(2):
                worker = service_app(role="worker", settings=settings)
                await stack.enter_async_context(worker.router.lifespan_context(worker))
        agent_view = await create_voice_agent(service, runs_kit, api)
        bearer = await runs_kit.bearer(service)
        available = await service.client.get(
            f"{service.api}/realtime", headers=bearer, params={"agent_id": agent_view["id"]}
        )
        checks = available.json()
        assert checks["maturity"] == "alpha" and checks["can_start"]
        assert (checks["audio_input_hz"], checks["audio_output_hz"]) == (input_hz, 24000)
        assert checks["supports_interruption"] is interruption
        assert checks["max_duration_seconds"] == settings.worker.realtime_max_duration_seconds
        assert not checks["accepts_images"] and checks["supported_image_types"] == []
        if api in {"xai.realtime", "google.realtime"}:
            assert checks["transports"] == ["websocket"]
            unsupported = await service.client.post(
                f"{service.api}/realtime/sessions",
                headers=bearer,
                json={"agent_id": agent_view["id"], "transport": "webrtc"},
            )
            assert unsupported.status_code == 400
            assert unsupported.json()["error"]["details"]["field"] == "transport"
        if transport == "webrtc":
            assert checks["transports"] == ["websocket", "webrtc"]
        response = await service.client.post(
            f"{service.api}/realtime/sessions",
            headers=bearer,
            json={
                "agent_id": agent_view["id"],
                "transport": transport,
                "audio": audio_enabled,
                "options": {"max_usage": {"requests": 12}},
                "memory": {"enabled": True},
            },
        )
        assert response.status_code == 201, response.text
        assert response.headers["cache-control"] == "no-store"
        ticket = response.json()
        assert ticket["maturity"] == "alpha"
        assert (ticket["audio_input_hz"], ticket["audio_output_hz"]) == (input_hz, 24000)
        assert ticket["max_duration_seconds"] == settings.worker.realtime_max_duration_seconds
        assert ticket["ice_servers"] == (
            [{"urls": ["turn:relay.example.org:3478"], "username": "voice", "credential": "fake-turn"}]
            if transport == "webrtc"
            else []
        )
        assert (await service.client.get(f"{service.api}/threads")).json()["items"] == []
        async with serve_http(ingress, lifespan="off") as url, asyncio.timeout(15):
            socket_url = url.replace("http://", "ws://", 1) + ticket["websocket_path"]
            protocols = [ticket["subprotocol"], ticket["ticket"]]
            async with connect(socket_url, subprotocols=protocols, origin="https://external-app.test") as socket:
                assert socket.subprotocol == ticket["subprotocol"]
                if transport == "webrtc":
                    await socket.send(json.dumps({"type": "offer", "sdp": OPENAI_AUDIO_OFFER}))
                receipt = json.loads(await socket.recv())
                assert receipt == {"type": "accepted", **{k: ticket[k] for k in ("run_id", "thread_id", "session_id")}}
                if transport == "webrtc":
                    assert json.loads(await socket.recv()) == {"type": "answer", "sdp": "answer-sdp"}
                    assert json.loads(await socket.recv()) == {
                        "type": "ready",
                        "transport": "webrtc",
                        "accepts_text": True,
                        "audio_input_hz": input_hz,
                        "audio_output_hz": 24000,
                        "supports_interruption": interruption,
                    }
                    assert "lookup_order" in provider.offer_tools & provider.tools
                else:
                    assert json.loads(await socket.recv()) == {
                        "type": "ready",
                        "audio_hz": 24000,
                        "accepts_text": True,
                        "audio_input_hz": input_hz,
                        "audio_output_hz": 24000,
                        "supports_interruption": interruption,
                    }
                if api == "openai.live":
                    assert provider.settings.get("openai_live_idle_audio", False) is (not audio_enabled)
                if api == "openai.realtime":
                    assert provider.settings["turn_detection"]["silence_duration_ms"] == 850
                await socket.send(json.dumps({"type": "text", "text": "Hello from an App."}))
                assert await provider.connection.outgoing.get() == "Hello from an App."
                pcm = b"\x01\x00" * 480
                if transport == "websocket" and audio_enabled:
                    await socket.send(pcm)
                    audio = await provider.connection.outgoing.get()
                    assert isinstance(audio, BinaryAudio) and audio.data == pcm
                    await provider.connection.incoming.put(AudioDelta(pcm))
                elif transport == "webrtc":
                    for call_id, allowed, expected in (
                        ("call_declined", False, "denied"),
                        ("call_revalidate", True, "Order changed"),
                        ("call_order", True, "Order shipped"),
                    ):
                        await provider.connection.incoming.put(
                            ToolCall(call_id, tool_name="lookup_order", args="{}", response_usage_follows=True)
                        )
                        await provider.connection.incoming.put(ResponseDone())
                        approval = json.loads(await socket.recv())
                        assert approval["type"] == "approval_required" and approval["call_id"] == call_id
                        assert calls == []
                        await socket.send(json.dumps({"type": "approval", "call_id": call_id, "approve": allowed}))
                        result = await provider.connection.outgoing.get()
                        assert isinstance(result, ToolResult) and result.tool_call_id == call_id
                        assert expected in result.output
                    assert calls == ["lookup_order"]
                await provider.connection.incoming.put(OutputTranscript("Hello!", is_final=True))
                await provider.connection.incoming.put(ResponseDone())
                if transport == "websocket" and audio_enabled:
                    assert await socket.recv() == pcm
                transcript = json.loads(await socket.recv())
                assert transcript["type"] == "transcript" and transcript["text"] == "Hello!"
                if api == "openai.live" and transport == "webrtc":
                    # The peer is still playing the tail of the exchange the provider just completed.
                    await socket.send('{"type":"interrupt"}')
                    received = []
                    while "playback_end" not in received:
                        received.append(json.loads(await socket.recv())["type"])
                    assert "interrupted" in received
                if transport == "webrtc":
                    await provider.connection.incoming.put(
                        ToolCall("call_pending", tool_name="lookup_order", args="{}", response_usage_follows=True)
                    )
                    await provider.connection.incoming.put(ResponseDone())
                    while (event := json.loads(await socket.recv()))["type"] == "transcript":
                        pass
                    assert event["type"] == "approval_required" and event["call_id"] == "call_pending"
                await socket.send('{"type":"stop"}')
                async for frame in socket:
                    assert json.loads(frame)["type"] in {"transcript", "sealed"}, frame
        run = await runs_kit.get_run(service, receipt["run_id"])
        session = (await service.client.get(f"{service.api}/sessions/{ticket['session_id']}")).json()
        assert session["memory"]["enabled"] is True
        assert run["options"]["max_usage"] == {"requests": 12}
        assert run["trigger"] == "realtime" and run["max_attempts"] == 1
        assert run["status"] == "completed"
        assert provider.hangups == (1 if transport == "webrtc" else 0)
        items = await runs_kit.items(service, run["id"])
        assert ("user", "Hello from an App.") in runs_kit.texts(items)
        if transport == "webrtc":
            outcomes = {i["content"]["toolCallId"]: i for i in items["items"] if i["kind"] == "tool_call"}
            assert outcomes["call_declined"]["content"]["outcome"] == "denied"
            assert outcomes["call_revalidate"]["state"] == "failed"
            assert "Order changed" in str(outcomes["call_revalidate"]["content"])
            assert outcomes["call_order"]["state"] == "completed"
            assert outcomes["call_pending"]["content"]["outcome"] == "expired"


@pytest.mark.parametrize(
    ("api", "adapter"), [("openai.realtime", OpenAIRealtimeModel), ("openai.live", OpenAILiveModel)]
)
async def test_two_pcm_interruptions_preserve_playback_and_model_guarantees(
    settings, serve, runs_kit, monkeypatch, api, adapter
):
    provider = ScriptedRealtimeProvider()
    monkeypatch.setattr(adapter, "connect", provider.connect)
    async with serve(role="all", settings=settings) as service:
        voice = await create_voice_agent(service, runs_kit, api)
        response = await service.client.post(f"{service.api}/realtime/sessions", json={"agent_id": voice["id"]})
        ticket = response.json()
        async with (
            serve_http(service.app, lifespan="off") as url,
            connect(
                url.replace("http://", "ws://") + ticket["websocket_path"],
                subprotocols=[ticket["subprotocol"], ticket["ticket"]],
            ) as ws,
            asyncio.timeout(15),
        ):
            assert json.loads(await ws.recv())["type"] == "accepted"
            assert json.loads(await ws.recv())["type"] == "ready"
            pcm = bytes(4800)  # 100 ms of 24 kHz PCM16 in each reply.
            for turn in range(2):
                item = f"audio{turn}"
                await ws.send(json.dumps({"type": "text", "text": item}))
                while await provider.connection.outgoing.get() != item:
                    pass
                await provider.connection.incoming.put(AudioDelta(pcm, item_id=item))
                frame = await ws.recv()
                while isinstance(frame, str):
                    assert json.loads(frame)["type"] == "transcript"
                    frame = await ws.recv()
                assert frame == pcm
                await ws.send(json.dumps({"type": "interrupt", "played_bytes": turn * len(pcm) + 1200}))
                if api == "openai.realtime":
                    cut = await provider.connection.outgoing.get()
                    assert isinstance(cut, TruncateOutput)
                    assert (cut.item_id, cut.audio_end_ms) == (item, 25)
                else:
                    assert provider.connection.outgoing.empty()
                control = json.loads(await ws.recv())["type"]
                while control == "transcript":
                    control = json.loads(await ws.recv())["type"]
                assert control == "interrupted"
                # A late tail from this item cannot play in the new epoch.
                await provider.connection.incoming.put(AudioDelta(bytes(240), item_id=item))
                await provider.connection.incoming.put(ResponseDone())
            await ws.send('{"type":"stop"}')
            async for frame in ws:
                assert json.loads(frame)["type"] in {"sealed", "transcript"}


async def test_split_realtime_stops_when_no_capable_worker_attaches(
    settings, serve, service_app, runs_kit, monkeypatch
):
    transport = import_module("a13n_service.runs.realtime_transport")
    monkeypatch.setattr(transport, "ATTACH_SECONDS", 1)
    async with AsyncExitStack() as stack:
        service = await stack.enter_async_context(serve(role="control", settings=settings))
        agent_view = await create_voice_agent(service, runs_kit)
        bearer = await runs_kit.bearer(service)
        response = await service.client.post(
            f"{service.api}/realtime/sessions", headers=bearer, json={"agent_id": agent_view["id"]}
        )
        assert response.status_code == 201, response.text
        ticket = response.json()
        async with serve_http(service.app, lifespan="off") as url, asyncio.timeout(10):
            socket_url = url.replace("http://", "ws://", 1) + ticket["websocket_path"]
            async with connect(socket_url, subprotocols=[ticket["subprotocol"], ticket["ticket"]]) as socket:
                accepted = json.loads(await socket.recv())
                assert accepted["type"] == "accepted"
                assert json.loads(await socket.recv())["type"] == "error"
        run = await runs_kit.get_run(service, accepted["run_id"])
        assert run["status"] == "failed"
        assert run["attempts"] == 0


@pytest.mark.parametrize("failed_peer", ["control", "worker", "lease"])
async def test_split_realtime_reconnects_after_peer_loss(
    settings, serve, service_app, runs_kit, monkeypatch, failed_peer
):
    settings = settings.model_copy(
        update={
            "worker": settings.worker.model_copy(update={"authority_seconds": 0.1, "scan_seconds": 0.05}),
        }
    )
    provider = ScriptedRealtimeProvider()
    monkeypatch.setattr(OpenAIRealtimeModel, "connect", provider.connect)
    transport = import_module("a13n_service.runs.realtime_transport")
    send = transport.Pipe.send
    failed = asyncio.Event()

    async def break_one_side(pipe, frame):
        if frame == b"ping" and failed.is_set() and pipe.inbox.endswith(f":{failed_peer}"):
            raise transport.unavailable()
        await send(pipe, frame)

    monkeypatch.setattr(transport.Pipe, "send", break_one_side)
    async with AsyncExitStack() as stack:
        service = await stack.enter_async_context(serve(role="control", settings=settings))
        worker = service_app(role="worker", settings=settings)
        await stack.enter_async_context(worker.router.lifespan_context(worker))
        agent_view = await create_voice_agent(service, runs_kit)
        bearer = await runs_kit.bearer(service)
        body = {"agent_id": agent_view["id"], "audio": False}
        async with serve_http(service.app, lifespan="off") as url, asyncio.timeout(20):
            for attempt in range(2):
                response = await service.client.post(f"{service.api}/realtime/sessions", headers=bearer, json=body)
                assert response.status_code == 201, response.text
                ticket = response.json()
                socket_url = url.replace("http://", "ws://", 1) + ticket["websocket_path"]
                async with connect(socket_url, subprotocols=[ticket["subprotocol"], ticket["ticket"]]) as socket:
                    accepted = json.loads(await socket.recv())
                    assert accepted["type"] == "accepted"
                    assert json.loads(await socket.recv())["type"] == "ready"
                    if attempt == 0:
                        await socket.send('{"type":"text","text":"Before the disconnect"}')
                        assert await provider.connection.outgoing.get() == "Before the disconnect"
                        await provider.connection.incoming.put(OutputTranscript("Saved", is_final=True))
                        await provider.connection.incoming.put(ResponseDone())
                        assert json.loads(await socket.recv())["type"] == "transcript"
                        if failed_peer == "lease":
                            async with short_session(service.runtime.storage) as session:
                                attempt_id = await session.scalar(
                                    select(AttemptRow.id).where(AttemptRow.run_id == accepted["run_id"])
                                )
                            (attempt_task,) = [
                                task for task in asyncio.all_tasks() if task.get_name() == f"attempt-{attempt_id}"
                            ]
                            attempt_task.cancel()
                            await asyncio.gather(attempt_task, return_exceptions=True)
                            async with transaction(service.runtime.storage) as session:
                                await session.execute(
                                    update(AttemptRow)
                                    .where(AttemptRow.id == attempt_id)
                                    .values(lease_expires_at=func.now())
                                )
                            await LeaseExpirer(service.runtime, batch=10)()
                        else:
                            failed.set()
                        while (frame := json.loads(await socket.recv()))["type"] == "transcript":
                            pass
                        assert frame["type"] == "error"
                        if failed_peer == "worker":
                            assert json.loads(await socket.recv()) == {
                                "type": "sealed",
                                "run_id": accepted["run_id"],
                                "status": "failed",
                                "reason": "realtime_transport_failed",
                            }
                        else:
                            with pytest.raises(ConnectionClosedError):
                                await socket.recv()
                    else:
                        await socket.send('{"type":"stop"}')
                        async for frame in socket:
                            assert json.loads(frame)["type"] in {"transcript", "sealed"}, frame
                while True:
                    run = await runs_kit.get_run(service, accepted["run_id"])
                    if run["status"] in {"completed", "cancelled", "failed"}:
                        break
                    await asyncio.sleep(0.05)
                assert run["attempts"] == 1
                if attempt == 0 and failed_peer == "lease":
                    assert run["status"] == "failed"
                    assert run["failure"]["code"] == "lease_expired"
                failed.clear()
                body["thread_id"] = accepted["thread_id"]
            assert run["status"] == "completed"


async def test_webrtc_rejects_data_channels_and_hangs_up_when_sideband_fails(settings, serve, runs_kit, monkeypatch):
    provider = ScriptedWebRTCProvider()
    provider.attach_error = ServiceError("conflict", "Sideband failed")
    monkeypatch.setattr(OpenAIRealtimeModel, "answer_webrtc_offer", provider.answer)
    monkeypatch.setattr(OpenAIRealtimeModel, "connect_webrtc", provider.attach)
    monkeypatch.setattr(OpenAIRealtimeModel, "hang_up", provider.hang_up)
    async with serve(role="all", settings=settings) as service:
        await runs_kit.pause_sweeps(service)
        agent_view = await create_voice_agent(service, runs_kit)
        bearer = await runs_kit.bearer(service)
        async with serve_http(service.app, lifespan="off") as url, asyncio.timeout(15):
            for sdp in (
                OPENAI_AUDIO_OFFER + " m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n",
                OPENAI_AUDIO_OFFER,
            ):
                response = await service.client.post(
                    f"{service.api}/realtime/sessions",
                    headers=bearer,
                    json={"agent_id": agent_view["id"], "transport": "webrtc"},
                )
                assert response.status_code == 201, response.text
                ticket = response.json()
                socket_url = url.replace("http://", "ws://", 1) + ticket["websocket_path"]
                async with connect(socket_url, subprotocols=[ticket["subprotocol"], ticket["ticket"]]) as socket:
                    await socket.send(json.dumps({"type": "offer", "sdp": sdp}))
                    first = json.loads(await socket.recv())
                    if sdp != OPENAI_AUDIO_OFFER:
                        assert "audio-only" in first["message"]
                        assert (await service.client.get(f"{service.api}/threads")).json()["items"] == []
                        continue
                    assert first["type"] == "accepted"
                    assert json.loads(await socket.recv())["type"] == "answer"
                    assert json.loads(await socket.recv())["type"] == "error"
                    assert (await runs_kit.get_run(service, first["run_id"]))["status"] == "failed"
                    assert provider.hangups == 1
                    run_id = first["run_id"]
                    async with transaction(service.runtime.storage) as db:
                        assert (
                            await db.scalar(
                                select(RealtimeCallRow.run_id).where(
                                    RealtimeCallRow.run_id == run_id, RealtimeCallRow.provider_session.is_(None)
                                )
                            )
                            == run_id
                        )
                        # Previously a successful hangup could persist JSON null instead of SQL NULL.
                        await db.execute(
                            update(RealtimeCallRow)
                            .where(RealtimeCallRow.run_id == run_id)
                            .values(provider_session=JSON.NULL)
                        )
                    await realtime_cleanup.reconcile(service.runtime)
                    async with short_session(service.runtime.storage) as db:
                        assert await db.get(RealtimeCallRow, run_id) is None
                    assert provider.hangups == 1


async def test_provider_call_defers_only_cleanup_errors(monkeypatch):
    from a13n_service.runs.attempts import LeaseLost

    runtime, lease, model = Mock(), Mock(), Mock()
    transaction = Mock(side_effect=OSError("Database is unavailable"))
    monkeypatch.setattr(realtime_cleanup, "transaction", transaction)

    await realtime_cleanup.provider_call(runtime, lease, model, None)
    with pytest.raises(OSError):
        await realtime_cleanup.provider_call(runtime, lease, model, "rtc_call")
    transaction.side_effect = LeaseLost()
    with pytest.raises(LeaseLost):
        await realtime_cleanup.provider_call(runtime, lease, model, None)


@pytest.mark.parametrize(
    ("api", "adapter"), [("openai.realtime", OpenAIRealtimeModel), ("azure.realtime", AzureRealtimeModel)]
)
async def test_orphaned_webrtc_call_survives_interrupted_cleanup(executing, runs_kit, monkeypatch, api, adapter):
    outcomes = Mock()
    monkeypatch.setattr(realtime_cleanup, "ORPHAN_HANGUPS", outcomes)
    service = executing
    await runs_kit.pause_sweeps(service)
    provider = ScriptedWebRTCProvider()
    monkeypatch.setattr("a13n_harness.live._WEBRTC_HANGUP_SECONDS", 0)
    monkeypatch.setattr(adapter, "answer_webrtc_offer", provider.answer)

    @asynccontextmanager
    async def attach(model, session, **kwargs):
        async with short_session(service.runtime.storage) as db:
            run = await db.get_one(RunRow, ticket["run_id"])
            attempt = await db.get_one(AttemptRow, run.current_attempt_id)
            assert attempt.status == "running" and attempt.harness_run_id
        async with provider.attach(session, **kwargs) as connected:
            yield connected

    async def stalled_hangup(session):
        await asyncio.Event().wait()

    monkeypatch.setattr(adapter, "connect_webrtc", attach)
    hangup = AsyncMock(side_effect=stalled_hangup)
    monkeypatch.setattr(adapter, "hang_up", hangup)
    voice = await create_voice_agent(service, runs_kit, api)
    response = await service.client.post(
        f"{service.api}/realtime/sessions", json={"agent_id": voice["id"], "transport": "webrtc"}
    )
    assert response.status_code == 201, response.text
    ticket = response.json()
    async with serve_http(service.app, lifespan="off") as url, asyncio.timeout(15):
        async with connect(
            url.replace("http://", "ws://") + ticket["websocket_path"],
            subprotocols=[ticket["subprotocol"], ticket["ticket"]],
        ) as socket:
            await socket.send(json.dumps({"type": "offer", "sdp": OPENAI_AUDIO_OFFER}))
            assert json.loads(await socket.recv())["type"] == "accepted"
            assert json.loads(await socket.recv())["type"] == "answer"
            assert json.loads(await socket.recv())["type"] == "ready"
            await socket.send('{"type":"stop"}')
            terminal = [json.loads(frame) async for frame in socket]
            assert terminal[-1]["status"] == "completed"
    run_id = ticket["run_id"]
    hangup.assert_awaited_once()
    async with short_session(service.runtime.storage) as db:
        run = await db.get_one(RunRow, run_id)
        assert run.status == "completed" and run.checkpoint

    model = (await service.client.get(f"{service.api}/models/voice")).json()
    provider_url = f"{service.api}/model-providers/{model['provider_id']}"
    saved = await service.client.get(provider_url)
    changed = await service.client.patch(
        provider_url,
        headers={"if-match": saved.headers["etag"]},
        json={
            "config": {**saved.json()["config"], "session_affinity_header": "x-thread"},
            "extra_headers": {"x-inference": "enabled"},
        },
    )
    assert changed.status_code == 200, changed.text

    hangup.reset_mock()
    entered = asyncio.Event()

    async def interrupted(session):
        assert session.session_id == provider.session.session_id
        entered.set()
        await asyncio.Event().wait()

    hangup.side_effect = interrupted
    async with asyncio.timeout(10):
        sweep = asyncio.create_task(realtime_cleanup.reconcile(service.runtime))
        try:
            await entered.wait()
            await realtime_cleanup.reconcile(service.runtime)
            assert hangup.await_count == 1
        finally:
            sweep.cancel()
            await asyncio.gather(sweep, return_exceptions=True)

    async def retry_now():
        async with transaction(service.runtime.storage) as db:
            call = await db.get_one(RealtimeCallRow, run_id)
            assert call.provider_session and call.hangup_retry_at
            call.hangup_retry_at = None

    outcomes.add.assert_not_called()  # Cancelling the sweep leaves the cleanup outcome unknown.
    await retry_now()
    hangup.side_effect = OSError("Provider temporarily unavailable")
    await realtime_cleanup.reconcile(service.runtime)
    outcomes.add.assert_called_once_with(1, {"result": "failed"})
    await retry_now()
    hangup.side_effect = None
    await realtime_cleanup.reconcile(service.runtime)
    outcomes.add.assert_called_with(1, {"result": "succeeded"})
    assert outcomes.add.call_count == 2
    assert hangup.await_count == 3
    assert all(args.args[0].session_id == provider.session.session_id for args in hangup.await_args_list)
    async with short_session(service.runtime.storage) as db:
        assert await db.get(RealtimeCallRow, run_id) is None


async def test_app_realtime_rejects_expired_tickets_and_closes_revoked_connections(
    settings, serve, runs_kit, monkeypatch
):
    settings = settings.model_copy(
        update={
            "worker": settings.worker.model_copy(update={"authority_seconds": 0.1}),
        }
    )
    provider = ScriptedRealtimeProvider()
    monkeypatch.setattr(OpenAIRealtimeModel, "connect", provider.connect)
    realtime = import_module("a13n_service.runs.realtime")
    async with serve(role="all", settings=settings) as service:
        agent_view = await create_voice_agent(service, runs_kit)
        issued = await service.client.post(
            "/api/v1/users/me/keys",
            json={"name": "voice", "workspace_id": service.tenant.workspace_id},
        )
        assert issued.status_code == 201, issued.text
        key = issued.json()["key"]
        bearer = {"authorization": f"Bearer {issued.json()['secret']}"}
        endpoint = f"{service.api}/realtime/sessions"
        body = {"agent_id": agent_view["id"], "audio": False}
        response = await service.client.post(endpoint, headers=bearer, json=body)
        assert response.status_code == 201, response.text
        ticket = response.json()
        with monkeypatch.context() as expired:
            expired.setattr(realtime, "TICKET_SECONDS", 0)
            response = await service.client.post(endpoint, headers=bearer, json=body)
        assert response.status_code == 201, response.text
        stale = response.json()
        async with serve_http(service.app, lifespan="off") as url, asyncio.timeout(15):
            socket_url = url.replace("http://", "ws://", 1) + ticket["websocket_path"]
            protocols = [ticket["subprotocol"], ticket["ticket"]]
            wrong_workspace = socket_url.replace(service.tenant.workspace_id, "ws_00000000000000000000")
            with pytest.raises(InvalidStatus):
                async with connect(wrong_workspace, subprotocols=protocols):
                    pytest.fail("A ticket cannot open another workspace")
            with pytest.raises(InvalidStatus):
                async with connect(socket_url, subprotocols=[stale["subprotocol"], stale["ticket"]]):
                    pytest.fail("An expired ticket cannot open a connection")
            spare = await service.client.post(endpoint, headers=bearer, json=body)
            assert spare.status_code == 201
            unused = spare.json()
            async with connect(socket_url, subprotocols=protocols) as socket:
                assert json.loads(await socket.recv())["type"] == "accepted"
                assert json.loads(await socket.recv())["type"] == "ready"
                with pytest.raises(InvalidStatus):
                    async with connect(socket_url, subprotocols=protocols):
                        pytest.fail("A used ticket cannot open a second connection")
                revoked = await service.client.delete(
                    f"/api/v1/users/me/keys/{key['id']}",
                    headers=runs_kit.if_match(key),
                )
                assert revoked.status_code == 200, revoked.text
                assert json.loads(await socket.recv())["type"] == "error"
                with pytest.raises(ConnectionClosedError):
                    await socket.recv()
            with pytest.raises(InvalidStatus):
                async with connect(socket_url, subprotocols=[unused["subprotocol"], unused["ticket"]]):
                    pytest.fail("A ticket cannot outlive the credential that issued it")


async def test_service_agent_can_run_realtime_from_a_thread_checkpoint(
    executing, scripted_model, runs_kit, monkeypatch
):
    service_agent = await runs_kit.create_agent(executing, scripted_model, instructions="Help with order tracking.")
    original_resolve = agent.resolve
    resolved_agents = []

    async def capture_resolution(*args, **kwargs):
        resolved = await original_resolve(*args, **kwargs)
        resolved_agents.append(resolved)
        return resolved

    # Keep the worker's real authorization and definition resolution.
    with monkeypatch.context() as patch:
        patch.setattr(agent, "resolve", capture_resolution)
        scripted_model.say("I can check order 42.")
        submitted = await runs_kit.start_thread(executing, service_agent, "My order number is 42.")
        run = await runs_kit.sealed(executing, submitted["run"]["id"])
    assert run["status"] == "completed"
    (resolved,) = resolved_agents
    await scripted_model.request()

    runtime = executing.runtime
    async with short_session(runtime.storage) as session:
        row = await session.get_one(RunRow, run["id"])
        pointer = checkpoints.StatePointer.model_validate(row.checkpoint)
    saved = await checkpoints.load_state(runtime.objects, pointer)
    assert saved is not None

    def build_agent(selected):
        return agent.build(selected, capabilities=lambda _: [], plugins=runtime.plugins, instrumentation=None)

    realtime_agent = replace(
        resolved,
        model=replace(
            resolved.model,
            provider=replace(resolved.model.provider, config={}),
            config=resolved.model.config.model_copy(
                update={"model_api": "openai.realtime", "model_name": "gpt-realtime-2.1"}
            ),
        ),
    )
    provider = ScriptedRealtimeProvider()
    monkeypatch.setattr(OpenAIRealtimeModel, "connect", provider.connect)
    async with (
        asyncio.timeout(10),
        agent.open_realtime(
            runtime,
            realtime_agent,
            build_agent(realtime_agent),
            bindings=RunBindings.embedded(),
            previous_state=saved.harness,
        ) as stream,
        stream,
    ):
        assert stream.thread_id == saved.harness.thread_id
        assert "My order number is 42." in str(provider.messages)
        await provider.connection.incoming.put(OutputTranscript("Your order has shipped.", is_final=True))
        await provider.connection.incoming.put(ResponseDone())
        async for item in stream:
            if isinstance(item, HarnessEvent) and isinstance(item.event, RealtimeTurnCompleteEvent):
                await stream.close()

    assert stream.result is not None and stream.result.status == "completed"
    updated = saved.model_copy(update={"harness": await stream.export_state()})
    restored = checkpoints.RunState.model_validate_json(updated.to_json_bytes())

    scripted_model.say("Tracking will follow.")
    async with AsyncExitStack() as stack:
        models = await agent.open_models(stack, runtime, resolved)
        ordinary = await build_agent(resolved).run(
            "What happens next?",
            previous_state=restored.harness,
            bindings=RunBindings.embedded(model_resolver=agent.model_resolver(resolved, models)),
        )
    assert ordinary.output == "Tracking will follow."
    request = await scripted_model.request()
    assert "Your order has shipped." in str(request["messages"])


async def test_concurrent_redemptions_on_two_controls(service, service_app, settings, runs_kit, realtime_agent):
    from a13n_service.infra.crypto import secret_hash
    from a13n_service.runs.realtime import ConnectionTicket, _redeem, _stop_abandoned
    from a13n_service.runs.tables import RealtimeTicketRow

    endpoint = f"{service.api}/realtime/sessions"
    workspace = service.tenant.workspace_id

    async def mint(thread=None):
        body = {"agent_id": realtime_agent["id"]}
        if thread:
            body["thread_id"] = thread
        response = await service.client.post(endpoint, json=body)
        assert response.status_code == 201, response.text
        raw = response.json()["ticket"]
        async with short_session(service.runtime.storage) as session:
            row = await session.get_one(RealtimeTicketRow, secret_hash(raw))
            return raw, ConnectionTicket.model_validate(row.request)

    async def race(runtimes, tickets):
        results = await asyncio.gather(
            *(_redeem(runtime, workspace, *ticket) for runtime, ticket in zip(runtimes, tickets, strict=True)),
            return_exceptions=True,
        )
        assert results.count(None) == 1
        return next(error for error in results if isinstance(error, ServiceError))

    second = service_app(role="control", settings=settings)
    async with second.router.lifespan_context(second):
        runtimes = service.runtime, second.state.runtime
        ticket = await mint()
        assert (await race(runtimes, (ticket, ticket))).code == "unauthenticated"
        await _stop_abandoned(service.runtime, workspace, ticket[1].run_id)
        tickets = await asyncio.gather(mint(ticket[1].thread_id), mint(ticket[1].thread_id))
        assert (await race(runtimes, tickets)).code == "conflict"
        async with short_session(service.runtime.storage) as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(RunRow).where(RunRow.thread_id == ticket[1].thread_id)
                )
                == 2
            )


async def test_control_delivery_with_saturated_pcm(service):
    from a13n_service.runs.realtime_transport import Bridge, Pipe

    first = service.runtime.realtime_bridge
    other = Bridge(first.redis)
    try:
        control = Pipe(first, "run_pressure", side="control")
        worker = Pipe(other, "run_pressure", side="worker")
        control.attempt_id = worker.attempt_id = "att_pressure"
        control.peer = other.id
        worker.peer = worker.control_id = first.id
        async with control.subscribe(), worker.subscribe(), asyncio.timeout(5):
            for seq in range(1, 129):
                await worker.send(b"b" + seq.to_bytes(2) * 480)
            # Wait for the final publication on the independent PCM subscription.
            while not control.audio or not control.audio[-1].endswith((128).to_bytes(2)):
                await asyncio.sleep(0.001)
            assert len(control.audio) == 32
            assert await control.receive() == b"b" + (97).to_bytes(2) * 480
            for frame in (b'j{"type":"approval","call_id":"one","approve":true}', b"stop"):
                await control.send(frame)
                assert await worker.receive() == frame
            sealed = b'j{"type":"sealed","status":"completed"}'
            await worker.send(sealed)
            while (frame := await control.receive()) != sealed:
                assert frame.startswith(b"b")
            await control.shutdown()
            assert await worker.receive() == b"closed"
    finally:
        await other.close()


async def test_crash_before_notice_checkpoint_redelivers_on_reconnect(
    settings, serve, service_app, runs_kit, monkeypatch
):
    from a13n_service.runs import inbox
    from a13n_service.runs.execute import _Attempt
    from a13n_service.runs.tables import InboxEntryRow, ThreadRow

    provider = ScriptedRealtimeProvider()
    monkeypatch.setattr(OpenAIRealtimeModel, "connect", provider.connect)
    calls = []

    def lookup() -> str:
        calls.append("lookup")
        return "receipt created"

    build = agent.build

    def with_tool(root, *, capabilities, **kwargs):
        return build(root, capabilities=lambda node: [*capabilities(node), Capability(tools=[lookup])], **kwargs)

    monkeypatch.setattr(agent, "build", with_tool)
    opening_started = asyncio.Event()
    deliver_opening = asyncio.Event()
    send = provider.connection.send

    async def hold_opening(content):
        if "opening draft" in str(content):
            assert "tool_receipt" in str(content)
            opening_started.set()
            await deliver_opening.wait()
        return await send(content)

    monkeypatch.setattr(provider.connection, "send", hold_opening)
    original = _Attempt._commit_cut
    staged = asyncio.Event()
    allow = asyncio.Event()

    async def hold(attempt, *args, **kwargs):
        if attempt.offers.open and not allow.is_set():
            staged.set()
            await allow.wait()
        return await original(attempt, *args, **kwargs)

    monkeypatch.setattr(_Attempt, "_commit_cut", hold)
    async with serve(role="control", settings=settings) as service:
        worker = service_app(role="worker", settings=settings)
        async with (
            worker.router.lifespan_context(worker),
            serve_http(service.app, lifespan="off") as url,
            asyncio.timeout(30),
        ):
            agent_view = await create_voice_agent(service, runs_kit)
            body = {"agent_id": agent_view["id"]}
            for attempt in range(2):
                response = await service.client.post(f"{service.api}/realtime/sessions", json=body)
                assert response.status_code == 201, response.text
                ticket = response.json()
                async with connect(
                    url.replace("http://", "ws://") + ticket["websocket_path"],
                    subprotocols=[ticket["subprotocol"], ticket["ticket"]],
                ) as socket:
                    assert json.loads(await socket.recv())["type"] == "accepted"
                    if attempt == 1:
                        await opening_started.wait()
                        with pytest.raises(TimeoutError):
                            await asyncio.wait_for(socket.recv(), 0.15)
                        deliver_opening.set()
                    assert json.loads(await socket.recv())["type"] == "ready"
                    if attempt == 0:
                        body.update(thread_id=ticket["thread_id"], draft="opening draft")
                        # Capture the completion of one independent tool operation.
                        await provider.connection.incoming.put(
                            ToolCall("call_lookup", tool_name="lookup", args="{}", response_usage_follows=True)
                        )
                        await provider.connection.incoming.put(ResponseDone())
                        result = await provider.connection.outgoing.get()
                        assert isinstance(result, ToolResult) and result.output == "receipt created"
                        await provider.connection.incoming.put(ResponseDone())
                        async with transaction(service.runtime.storage) as session:
                            thread = await session.get_one(ThreadRow, ticket["thread_id"], with_for_update=True)
                            run = await session.get_one(RunRow, ticket["run_id"])
                            entry = await inbox.append_tool_result(
                                session, thread, run, {"execution_id": "tool_receipt", "status": "succeeded"}
                            )
                            notice_id = entry.id
                            service.runtime.mailbox.notify(session, entry)
                    while "tool_receipt" not in str(await provider.connection.outgoing.get()):
                        pass
                    if attempt == 0:
                        await staged.wait()
                        async with short_session(service.runtime.storage) as session:
                            run = await session.get_one(RunRow, ticket["run_id"])
                            attempt_id = run.current_attempt_id
                        task = next(task for task in asyncio.all_tasks() if task.get_name() == f"attempt-{attempt_id}")
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        async with transaction(service.runtime.storage) as session:
                            await session.execute(
                                update(AttemptRow)
                                .where(AttemptRow.id == attempt_id)
                                .values(lease_expires_at=func.now())
                            )
                        expirer = LeaseExpirer(service.runtime, batch=10)
                        while True:
                            await expirer()
                            async with short_session(service.runtime.storage) as session:
                                if (await session.get_one(RunRow, ticket["run_id"])).status == "failed":
                                    assert (await session.get_one(InboxEntryRow, notice_id)).status == "pending"
                                    break
                            await asyncio.sleep(0.05)
                        allow.set()
                    else:
                        # The mic is usable before the opening reply completes.
                        pcm = bytes(960)
                        await socket.send(pcm)
                        audio = await provider.connection.outgoing.get()
                        assert isinstance(audio, BinaryAudio) and audio.data == pcm
                        await socket.send('{"type":"stop"}')
                        terminal = [json.loads(frame) async for frame in socket]
                        assert terminal[-1]["type"] == "sealed"
            async with short_session(service.runtime.storage) as session:
                notice = await session.get_one(InboxEntryRow, notice_id)
                assert notice.status == "consumed" and notice.incorporated_checkpoint_seq is not None
                assert notice.assigned_run_id == ticket["run_id"]
            visible = runs_kit.texts(await runs_kit.items(service, ticket["run_id"]))
            assert [text for role, text in visible if role == "user"] == ["opening draft"]
            assert all("tool_receipt" not in text for _, text in visible)
            assert calls == ["lookup"]


@pytest.mark.parametrize(("after_dispatch", "requires_approval"), [(False, True), (True, True), (False, False)])
async def test_approval_close_straddles_dispatch_gate(
    settings, serve, service_app, runs_kit, monkeypatch, after_dispatch, requires_approval
):
    from a13n_harness.live import HarnessLiveStream
    from a13n_service.runs.boundaries import Boundaries
    from pydantic_ai.messages import ToolReturnPart

    dispatched = asyncio.Event()
    gate = asyncio.Event()
    resume = asyncio.Event()
    ended = asyncio.Event()
    provider = ScriptedRealtimeProvider()
    monkeypatch.setattr(OpenAIRealtimeModel, "connect", provider.connect)

    async def lookup_order() -> str:
        dispatched.set()
        try:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")
        finally:
            ended.set()

    build = agent.build

    def with_tool(root, *, capabilities, **kwargs):
        return build(
            root,
            capabilities=lambda node: [
                *capabilities(node),
                Capability(tools=[Tool(lookup_order, requires_approval=requires_approval)]),
            ],
            **kwargs,
        )

    monkeypatch.setattr(agent, "build", with_tool)
    original = Boundaries.before_tool_execute
    close_requested = asyncio.Event()
    usage = None

    async def pause(boundary, ctx, *, call, tool_def, args):
        nonlocal usage
        usage = ctx.usage
        if boundary.before_dispatch and not after_dispatch:
            verify = boundary.before_dispatch

            async def hold(call_id):
                gate.set()
                await close_requested.wait()
                try:
                    return await verify(call_id)
                finally:
                    resume.set()

            boundary.before_dispatch = hold
        return await original(boundary, ctx, call=call, tool_def=tool_def, args=args)

    monkeypatch.setattr(Boundaries, "before_tool_execute", pause)
    if not after_dispatch:
        close = HarnessLiveStream.close

        async def close_after_gate(stream):
            # Let the dispatch hook observe a closed channel while the native session is still open.
            close_requested.set()
            await resume.wait()
            await close(stream)

        monkeypatch.setattr(HarnessLiveStream, "close", close_after_gate)
    async with serve(role="control", settings=settings) as service:
        worker = service_app(role="worker", settings=settings)
        async with (
            worker.router.lifespan_context(worker),
            serve_http(service.app, lifespan="off") as url,
            asyncio.timeout(25),
        ):
            voice = await create_voice_agent(service, runs_kit)
            response = await service.client.post(f"{service.api}/realtime/sessions", json={"agent_id": voice["id"]})
            ticket = response.json()
            async with connect(
                url.replace("http://", "ws://") + ticket["websocket_path"],
                subprotocols=[ticket["subprotocol"], ticket["ticket"]],
            ) as ws:
                assert json.loads(await ws.recv())["type"] == "accepted"
                assert json.loads(await ws.recv())["type"] == "ready"
                await provider.connection.incoming.put(
                    ToolCall("call_order", tool_name="lookup_order", args="{}", response_usage_follows=True)
                )
                await provider.connection.incoming.put(ResponseDone())
                if requires_approval:
                    assert json.loads(await ws.recv())["type"] == "approval_required"
                    await ws.send('{"type":"approval","call_id":"call_order","approve":true}')
                await (dispatched if after_dispatch else gate).wait()
                if after_dispatch:
                    response = await service.client.post(f"{service.api}/runs/{ticket['run_id']}/interrupt")
                    assert response.status_code == 200
                    await runs_kit.sealed(service, ticket["run_id"])
            run = await runs_kit.sealed(service, ticket["run_id"])
            assert run["status"] == ("cancelled" if after_dispatch else "completed")
            assert dispatched.is_set() == after_dispatch
            if after_dispatch:
                assert ended.is_set()
            else:
                assert usage is not None and usage.tool_calls == 0
                async with short_session(service.runtime.storage) as db:
                    row = await db.get_one(RunRow, run["id"])
                    pointer = checkpoints.StatePointer.model_validate(row.checkpoint)
                state = await checkpoints.load_state(service.runtime.objects, pointer)
                assert state is not None
                assert [
                    part.outcome
                    for message in state.harness.message_history
                    for part in message.parts
                    if isinstance(part, ToolReturnPart) and part.tool_call_id == "call_order"
                ] == ["interrupted"]
            items = await runs_kit.items(service, ticket["run_id"])
            item = next(i for i in items["items"] if i["content"].get("toolCallId") == "call_order")
            assert item["content"]["outcome"] == ("interrupted" if after_dispatch else "expired")


async def test_provider_reconnect_expires_pending_approval_without_running_tool(executing, runs_kit, monkeypatch):
    provider = ScriptedRealtimeProvider()
    monkeypatch.setattr(OpenAIRealtimeModel, "connect", provider.connect)
    lookup = Mock()

    async def lookup_order() -> str:
        lookup()
        return "order found"

    build = agent.build

    def with_tool(root, *, capabilities, **kwargs):
        return build(
            root,
            capabilities=lambda node: [
                *capabilities(node),
                Capability(tools=[Tool(lookup_order, requires_approval=True)]),
            ],
            **kwargs,
        )

    monkeypatch.setattr(agent, "build", with_tool)
    voice = await create_voice_agent(executing, runs_kit)
    response = await executing.client.post(f"{executing.api}/realtime/sessions", json={"agent_id": voice["id"]})
    assert response.status_code == 201, response.text
    ticket = response.json()
    async with (
        serve_http(executing.app, lifespan="off") as url,
        connect(
            url.replace("http://", "ws://") + ticket["websocket_path"],
            subprotocols=[ticket["subprotocol"], ticket["ticket"]],
        ) as socket,
        asyncio.timeout(15),
    ):
        assert json.loads(await socket.recv())["type"] == "accepted"
        assert json.loads(await socket.recv())["type"] == "ready"
        await provider.connection.incoming.put(
            ToolCall("call_order", tool_name="lookup_order", args="{}", response_usage_follows=True)
        )
        await provider.connection.incoming.put(ResponseDone())
        approval = json.loads(await socket.recv())
        assert approval["type"] == "approval_required" and approval["call_id"] == "call_order"

        await provider.connection.incoming.put(RealtimeSessionReconnectEvent(state_restored=False))
        assert json.loads(await socket.recv()) == {"type": "approval_expired", "call_id": "call_order"}
        await socket.send('{"type":"approval","call_id":"call_order","approve":true}')
        await socket.send('{"type":"stop"}')
        assert json.loads(await socket.recv())["type"] == "sealed"

    run = await runs_kit.sealed(executing, ticket["run_id"])
    assert run["status"] == "completed"
    lookup.assert_not_called()
    items = await runs_kit.items(executing, run["id"])
    tool = next(i for i in items["items"] if i["content"].get("toolCallId") == "call_order")
    assert tool["content"]["outcome"] == "expired"


async def test_missed_sealed_receipt_recovers_via_http(settings, serve, service_app, runs_kit, monkeypatch):
    from a13n_service.runs.realtime_transport import Pipe

    provider = ScriptedRealtimeProvider()
    monkeypatch.setattr(OpenAIRealtimeModel, "connect", provider.connect)
    send = Pipe.send
    dropped = asyncio.Event()

    async with serve(role="control", settings=settings) as service:

        async def drop_receipt(pipe, frame):
            if frame.startswith(b"j") and (event := json.loads(frame[1:]))["type"] == "sealed":
                run = await runs_kit.get_run(service, event["run_id"])
                assert run["status"] == event["status"] == "completed"
                dropped.set()
                return
            await send(pipe, frame)

        monkeypatch.setattr(Pipe, "send", drop_receipt)
        worker = service_app(role="worker", settings=settings)
        async with (
            worker.router.lifespan_context(worker),
            serve_http(service.app, lifespan="off") as url,
            asyncio.timeout(15),
        ):
            agent_view = await create_voice_agent(service, runs_kit)
            body = {"agent_id": agent_view["id"]}
            response = await service.client.post(f"{service.api}/realtime/sessions", json=body)
            assert response.status_code == 201, response.text
            ticket = response.json()
            async with connect(
                url.replace("http://", "ws://") + ticket["websocket_path"],
                subprotocols=[ticket["subprotocol"], ticket["ticket"]],
            ) as socket:
                assert json.loads(await socket.recv())["type"] == "accepted"
                assert json.loads(await socket.recv())["type"] == "ready"
                await socket.send('{"type":"stop"}')
                async for frame in socket:
                    assert json.loads(frame)["type"] != "sealed"
            assert dropped.is_set()
            run = await runs_kit.get_run(service, ticket["run_id"])
            assert run["status"] == "completed"
            eligibility = await service.client.get(
                f"{service.api}/realtime", params={**body, "thread_id": ticket["thread_id"]}
            )
            assert eligibility.json()["can_start"]


async def test_unavailable_bridge_refuses_redemption_without_starting_a_run(service, realtime_agent, monkeypatch):
    from redis.exceptions import ConnectionError as RedisConnectionError

    response = await service.client.post(f"{service.api}/realtime/sessions", json={"agent_id": realtime_agent["id"]})
    ticket = response.json()
    monkeypatch.setattr(service.runtime.realtime_bridge.redis, "ping", AsyncMock(side_effect=RedisConnectionError()))
    async with serve_http(service.app, lifespan="off") as url:
        with pytest.raises(InvalidStatus) as denied:
            async with connect(
                url.replace("http://", "ws://") + ticket["websocket_path"],
                subprotocols=[ticket["subprotocol"], ticket["ticket"]],
            ):
                pytest.fail("Redis failure must refuse redemption")
    assert denied.value.response.status_code == 503
    async with short_session(service.runtime.storage) as session:
        assert await session.get(RunRow, ticket["run_id"]) is None


def test_realtime_settings_expose_inference_and_protect_service_owned_fields():
    from a13n_service.providers.model_settings import check_settings, settings_schema

    schema = settings_schema("openai.realtime")
    assert check_settings(schema, {"max_tokens": 256}, field="settings") == {"max_tokens": 256}
    for key in ("instructions", "tools", "api_key", "extra_headers", "transport"):
        with pytest.raises(ServiceError):
            check_settings(schema, {key: "override"}, field="settings")


async def test_voice_fork_redeems_into_a_new_thread_at_the_selected_checkpoint(executing, runs_kit, monkeypatch):
    from a13n_service.app_provenance import AppProvenance
    from a13n_service.runs.sessions import new_session
    from a13n_service.runs.threads import new_thread

    provider = ScriptedRealtimeProvider()
    monkeypatch.setattr(OpenAIRealtimeModel, "connect", provider.connect)
    agent_view = await create_voice_agent(executing, runs_kit)
    api = executing.api
    client = executing.client

    async with serve_http(executing.app, lifespan="off") as url, asyncio.timeout(35):

        async def talk(text, **selection):
            response = await client.post(
                f"{api}/realtime/sessions",
                json={
                    "agent_id": agent_view["id"],
                    "draft": text,
                    **selection,
                },
            )
            assert response.status_code == 201, response.text
            ticket = response.json()
            if "fork_run_id" in selection:
                assert (await client.get(f"{api}/threads/{ticket['thread_id']}")).status_code == 404
            async with connect(
                url.replace("http://", "ws://") + ticket["websocket_path"],
                subprotocols=[ticket["subprotocol"], ticket["ticket"]],
            ) as socket:
                assert json.loads(await socket.recv())["type"] == "accepted"
                assert json.loads(await socket.recv())["type"] == "ready"
                before = str(provider.messages)
                assert await provider.connection.outgoing.get() == text
                await provider.connection.incoming.put(OutputTranscript("Acknowledged", is_final=True))
                await provider.connection.incoming.put(ResponseDone())
                assert json.loads(await socket.recv())["type"] == "transcript"
                await socket.send('{"type":"stop"}')
                events = [json.loads(frame) async for frame in socket]
                assert events[-1]["status"] == "completed"
            return await runs_kit.get_run(executing, ticket["run_id"]), before

        tenant = executing.tenant
        provenance = AppProvenance("voice-app", {"instance": "one"})
        owner = new_session(tenant.organization_id, tenant.workspace_id, tenant.principal_id, provenance=provenance)
        thread = new_thread(owner, mcp_headers={})
        async with transaction(executing.runtime.storage) as db:
            db.add_all((owner, thread))
        root, _ = await talk("Original branch point", thread_id=thread.id)
        invalid_selection = await client.get(
            f"{api}/realtime", params={"agent_id": agent_view["id"], "fork_run_id": root["id"], "model": "other"}
        )
        assert invalid_selection.status_code == 400
        assert invalid_selection.json()["error"]["details"]["field"] == "model"
        newer, _ = await talk("Later source message", thread_id=root["thread_id"])
        branch, inherited = await talk("New branch message", fork_run_id=root["id"])
        assert branch["session_id"] == root["session_id"] and branch["thread_id"] != root["thread_id"]
        assert branch["trigger"] == "realtime" and branch["lineage"] == "fork" and branch["parent_run_id"] == root["id"]
        for run in (root, newer, branch):
            assert run["app_id"] == "voice-app" and run["trigger_app_id"] is None
        async with short_session(executing.runtime.storage) as db:
            for run in (newer, branch):
                assert (await db.get_one(RunRow, run["id"])).app_context == provenance.app_context
        assert branch["agent_revision_id"] == root["agent_revision_id"]
        assert "Original branch point" in inherited and "Later source message" not in inherited
        source_thread = (await client.get(f"{api}/threads/{root['thread_id']}")).json()
        assert source_thread["last_run_id"] == newer["id"]


async def test_stale_run_removed_during_reconcile_does_not_skip_next_run(settings, serve, runs_kit, monkeypatch):
    from datetime import timedelta

    from a13n_service.runs import realtime
    from a13n_service.runs.realtime import ConnectionTicket
    from a13n_service.runs.tables import RealtimeTicketRow

    async with serve(role="control", settings=settings) as service:
        await runs_kit.pause_sweeps(service)
        voice = await create_voice_agent(service, runs_kit)
        workspace = service.tenant.workspace_id
        ids = []
        for _ in range(2):
            response = await service.client.post(f"{service.api}/realtime/sessions", json={"agent_id": voice["id"]})
            assert response.status_code == 201
            raw = response.json()["ticket"]
            async with short_session(service.runtime.storage) as db:
                row = await db.get_one(RealtimeTicketRow, realtime.secret_hash(raw))
                ticket = ConnectionTicket.model_validate(row.request)
            await realtime._redeem(service.runtime, workspace, raw, ticket)
            ids.append(ticket.run_id)
        async with transaction(service.runtime.storage) as db:
            await db.execute(
                update(RunRow).where(RunRow.id.in_(ids)).values(available_at=func.now() - timedelta(minutes=1))
            )
        stop = realtime._stop_abandoned
        visited = []

        async def remove_first(runtime, workspace_id, run_id):
            visited.append(run_id)
            await stop(runtime, workspace_id, run_id if len(visited) > 1 else "run_missing")

        monkeypatch.setattr(realtime, "_stop_abandoned", remove_first)
        await realtime_cleanup.reconcile(service.runtime)
        assert set(visited) == set(ids)
        assert (await runs_kit.get_run(service, visited[1]))["status"] == "failed"
