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
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.model.openai import DEFINITION as OPENAI
from a13n_harness.providers.model.xai import DEFINITION as XAI
from a13n_service.infra.crypto import KeyRing, SecretLocation
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.registry import Registry
from a13n_service.resources.models.realtime import open_realtime_model
from a13n_service.resources.models.schemas import ModelConfig
from a13n_service.resources.models.service import ResolvedModel
from a13n_service.resources.providers.service import ResolvedProvider
from a13n_service.runs import agent, checkpoints
from a13n_service.runs.realtime import LABEL, Channel, relay
from a13n_service.runs.tables import RunRow
from a13n_service.settings import Providers
from pydantic import SecretStr
from pydantic_ai import Tool
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import BinaryAudio, ModelMessage, RealtimeTurnCompleteEvent
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.realtime import RealtimeModelSettings, WebRTCAnswer, WebRTCSession
from pydantic_ai.realtime.codec import (
    AudioDelta,
    OutputTranscript,
    RealtimeCodecEvent,
    RealtimeConnection,
    RealtimeInput,
    ResponseDone,
    TextContext,
    ToolCall,
    ToolResult,
)
from pydantic_ai.realtime.lifecycle import InferredLifecycle, LifecycleEvent, RealtimeFrame
from pydantic_ai.realtime.openai import OpenAIRealtimeModel
from pydantic_ai.realtime.openai_live import OpenAILiveModel
from pydantic_ai.realtime.xai import XaiRealtimeModel
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
        "xai.realtime": ("xai", "grok-voice-latest"),
    }[model_api]
    provider = await service.client.post(
        f"{service.api}/model-providers",
        json={"type": provider_type, "name": "Realtime", "config": {}, "credential": {"api_key": "test-key"}},
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


@pytest.mark.parametrize("preview", [False, True])
async def test_http_submission_requires_a_realtime_connection(service, runs_kit, realtime_agent, monkeypatch, preview):
    runtime = replace(service.runtime, realtime_channels={}) if preview else service.runtime
    monkeypatch.setattr(service.app.state, "runtime", runtime)
    response = await service.client.post(
        f"{service.api}/threads",
        json=runs_kit.message(realtime_agent, "Hello"),
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["details"]["reason"] == "This model requires a realtime conversation"
    assert (await service.client.get(f"{service.api}/threads")).json()["items"] == []


async def test_realtime_budget_restriction_is_reported_before_start(service, realtime_agent, monkeypatch):
    runtime = replace(service.runtime, realtime_channels={}, admission=Mock())
    monkeypatch.setattr(service.app.state, "runtime", runtime)
    response = await service.client.get(f"{service.api}/realtime", params={"agent_id": realtime_agent["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["can_start"] is False
    assert "per-call budget reservations" in response.json()["reason"]


async def test_failed_plan_finishes_an_accepted_realtime_connection(service, runs_kit, realtime_agent, monkeypatch):
    channel = Channel(service.tenant.principal_id, service.tenant.workspace_id)
    runtime = replace(service.runtime, realtime_channels={"test-connection": channel})
    monkeypatch.setattr(service.app.state, "runtime", runtime)
    submitted = await runs_kit.start_thread(
        service, realtime_agent, "Start", options={"labels": {LABEL: "test-connection"}}
    )
    execution = import_module("a13n_service.runs.execute")
    monkeypatch.setattr(execution, "_plan", AsyncMock(side_effect=ServiceError("forbidden", "Execution denied")))
    async with asyncio.timeout(5):
        await (await runs_kit.attempt(service, runtime=runtime))
        socket = Mock(receive=AsyncMock(side_effect=asyncio.Event().wait), send_json=AsyncMock())
        await relay(socket, channel)
    socket.send_json.assert_awaited_once_with({"type": "error", "message": "The realtime run failed"})
    assert channel.finished.is_set()
    assert (await runs_kit.get_run(service, submitted["run"]["id"]))["status"] == "failed"


@pytest.mark.parametrize(
    ("provider_type", "api", "name", "adapter"),
    [
        ("openai", "openai.realtime", "gpt-realtime-2.1", OpenAIRealtimeModel),
        ("openai", "openai.live", "gpt-live-1", OpenAILiveModel),
        ("xai", "xai.realtime", "grok-voice-latest", XaiRealtimeModel),
    ],
)
async def test_primary_model_selects_its_realtime_adapter(provider_type, api, name, adapter):
    location = SecretLocation("org_test", "model_providers", "credential", "mp_test")
    keys = KeyRing(active_key_id="test", keys={"test": SecretStr(base64.b64encode(bytes(32)).decode())})
    provider = ResolvedProvider(
        id="mp_test",
        version=1,
        type=provider_type,
        config={},
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
    registry = Registry.of((OPENAI, XAI))
    options = {"registry": registry, "keys": keys, "settings": Providers()}
    async with open_realtime_model(model, policy=EndpointPolicy(), **options) as native:
        assert type(native) is adapter and native.model_name == name

    restricted = EndpointPolicy().for_run(RunConfiguration(allowed_hosts=frozenset()))
    with pytest.raises(ServiceError, match="outbound policy"):
        async with open_realtime_model(model, policy=restricted, **options):
            pytest.fail("a disabled destination was accepted")

    custom = replace(model, provider=replace(provider, config={"base_url": "https://gateway.example/v1"}))
    with pytest.raises(ServiceError, match="official API endpoint"):
        async with open_realtime_model(custom, policy=EndpointPolicy(), **options):
            pytest.fail("a custom realtime destination was accepted")


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
        self.tools: set[str] = set()

    @asynccontextmanager
    async def connect(
        self,
        *,
        messages: Sequence[ModelMessage],
        model_settings: RealtimeModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> AsyncIterator[RealtimeConnection]:
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
    ("api", "transport", "adapter"),
    [
        ("openai.realtime", "websocket", OpenAIRealtimeModel),
        ("openai.realtime", "webrtc", OpenAIRealtimeModel),
        ("openai.live", "webrtc", OpenAILiveModel),
        ("xai.realtime", "websocket", XaiRealtimeModel),
    ],
)
async def test_app_opens_realtime_over_http_and_websocket(
    settings, serve, runs_kit, monkeypatch, api, transport, adapter
):
    enabled = settings.model_copy(update={"server": settings.server.model_copy(update={"realtime_preview": True})})
    provider = ScriptedWebRTCProvider()
    monkeypatch.setattr(adapter, "connect", provider.connect)
    calls = []
    if transport == "webrtc":
        monkeypatch.setattr(adapter, "answer_webrtc_offer", provider.answer)
        monkeypatch.setattr(adapter, "connect_webrtc", provider.attach)
        monkeypatch.setattr(adapter, "hang_up", provider.hang_up)
        build = agent.build

        def lookup_order() -> str:
            calls.append("lookup_order")
            return "Order shipped"

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
    async with serve(role="all", settings=enabled) as service:
        agent_view = await create_voice_agent(service, runs_kit, api)
        bearer = await runs_kit.bearer(service)
        if api == "xai.realtime":
            available = await service.client.get(
                f"{service.api}/realtime", headers=bearer, params={"agent_id": agent_view["id"]}
            )
            assert available.json()["transports"] == ["websocket"]
            unsupported = await service.client.post(
                f"{service.api}/realtime/sessions",
                headers=bearer,
                json={"agent_id": agent_view["id"], "transport": "webrtc"},
            )
            assert unsupported.status_code == 400
            assert unsupported.json()["error"]["details"]["field"] == "transport"
        if transport == "webrtc":
            available = await service.client.get(
                f"{service.api}/realtime", headers=bearer, params={"agent_id": agent_view["id"]}
            )
            assert available.json()["transports"] == ["websocket", "webrtc"]
        response = await service.client.post(
            f"{service.api}/realtime/sessions",
            headers=bearer,
            json={"agent_id": agent_view["id"], "transport": transport},
        )
        assert response.status_code == 201, response.text
        assert response.headers["cache-control"] == "no-store"
        ticket = response.json()
        assert (await service.client.get(f"{service.api}/threads")).json()["items"] == []
        async with serve_http(service.app, lifespan="off") as url, asyncio.timeout(15):
            socket_url = url.replace("http://", "ws://", 1) + ticket["websocket_path"]
            protocols = [ticket["subprotocol"], ticket["ticket"]]
            wrong_workspace = socket_url.replace(service.tenant.workspace_id, "ws_00000000000000000000")
            with pytest.raises(InvalidStatus):
                async with connect(wrong_workspace, subprotocols=protocols):
                    pytest.fail("A ticket cannot open another workspace")
            async with connect(socket_url, subprotocols=protocols, origin="https://external-app.test") as socket:
                assert socket.subprotocol == ticket["subprotocol"]
                if transport == "webrtc":
                    await socket.send(json.dumps({"type": "offer", "sdp": OPENAI_AUDIO_OFFER}))
                receipt = json.loads(await socket.recv())
                assert receipt["type"] == "accepted"
                if transport == "webrtc":
                    assert json.loads(await socket.recv()) == {"type": "answer", "sdp": "answer-sdp"}
                    assert json.loads(await socket.recv()) == {
                        "type": "ready",
                        "transport": "webrtc",
                        "accepts_text": True,
                    }
                    assert "lookup_order" in provider.offer_tools & provider.tools
                else:
                    assert json.loads(await socket.recv()) == {"type": "ready", "audio_hz": 24000, "accepts_text": True}
                with pytest.raises(InvalidStatus):
                    async with connect(socket_url, subprotocols=protocols):
                        pytest.fail("A used ticket cannot open a second connection")
                await socket.send(json.dumps({"type": "text", "text": "Hello from an App."}))
                assert await provider.connection.outgoing.get() == "Hello from an App."
                pcm = b"\x01\x00" * 480
                if transport == "websocket":
                    await socket.send(pcm)
                    audio = await provider.connection.outgoing.get()
                    assert isinstance(audio, BinaryAudio) and audio.data == pcm
                    await provider.connection.incoming.put(AudioDelta(pcm))
                else:
                    await provider.connection.incoming.put(
                        ToolCall("call_order", tool_name="lookup_order", args="{}", response_usage_follows=True)
                    )
                    await provider.connection.incoming.put(ResponseDone())
                    approval = json.loads(await socket.recv())
                    assert approval["type"] == "approval_required" and approval["call_id"] == "call_order"
                    assert calls == []
                    await socket.send('{"type":"approval","call_id":"call_order","approve":true}')
                    result = await provider.connection.outgoing.get()
                    assert isinstance(result, ToolResult) and "Order shipped" in result.output
                    assert calls == ["lookup_order"]
                await provider.connection.incoming.put(OutputTranscript("Hello!", is_final=True))
                await provider.connection.incoming.put(ResponseDone())
                if transport == "websocket":
                    assert await socket.recv() == pcm
                transcript = json.loads(await socket.recv())
                assert transcript["type"] == "transcript" and transcript["text"] == "Hello!"
                await socket.send('{"type":"stop"}')
                async for frame in socket:
                    assert json.loads(frame)["type"] == "transcript", frame
        run = await runs_kit.get_run(service, receipt["run_id"])
        assert run["status"] == "completed"
        assert provider.hangups == (1 if transport == "webrtc" else 0)
        assert ("user", "Hello from an App.") in runs_kit.texts(await runs_kit.items(service, run["id"]))


async def test_webrtc_rejects_data_channels_and_hangs_up_when_sideband_fails(settings, serve, runs_kit, monkeypatch):
    enabled = settings.model_copy(update={"server": settings.server.model_copy(update={"realtime_preview": True})})
    provider = ScriptedWebRTCProvider()
    provider.attach_error = ServiceError("conflict", "Sideband failed")
    monkeypatch.setattr(OpenAIRealtimeModel, "answer_webrtc_offer", provider.answer)
    monkeypatch.setattr(OpenAIRealtimeModel, "connect_webrtc", provider.attach)
    monkeypatch.setattr(OpenAIRealtimeModel, "hang_up", provider.hang_up)
    async with serve(role="all", settings=enabled) as service:
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


async def test_app_realtime_rejects_expired_tickets_and_closes_revoked_connections(
    settings, serve, runs_kit, monkeypatch
):
    enabled = settings.model_copy(
        update={
            "server": settings.server.model_copy(update={"realtime_preview": True}),
            "worker": settings.worker.model_copy(update={"authority_seconds": 0.1}),
        }
    )
    provider = ScriptedRealtimeProvider()
    monkeypatch.setattr(OpenAIRealtimeModel, "connect", provider.connect)
    realtime = import_module("a13n_service.runs.realtime")
    async with serve(role="all", settings=enabled) as service:
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
            with pytest.raises(InvalidStatus):
                async with connect(socket_url, subprotocols=[stale["subprotocol"], stale["ticket"]]):
                    pytest.fail("An expired ticket cannot open a connection")
            spare = await service.client.post(endpoint, headers=bearer, json=body)
            assert spare.status_code == 201
            unused = spare.json()
            async with connect(socket_url, subprotocols=[ticket["subprotocol"], ticket["ticket"]]) as socket:
                assert json.loads(await socket.recv())["type"] == "accepted"
                assert json.loads(await socket.recv())["type"] == "ready"
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

    def lookup_order(order_id: int) -> str:
        """Look up the current shipping status for an order."""
        assert order_id == 42
        return "Order 42 has shipped."

    def build_agent(selected):
        return agent.build(
            selected,
            capabilities=lambda _: [Capability(tools=[lookup_order])],
            plugins=runtime.plugins,
            instrumentation=None,
        )

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
    assert realtime_agent.config.model == realtime_agent.model.key
    provider = ScriptedRealtimeProvider()
    monkeypatch.setattr(OpenAIRealtimeModel, "connect", provider.connect)
    connection = provider.connection
    microphone = bytes(480)
    speaker = b"\x01\x00" * 240

    async with (
        asyncio.timeout(10),
        agent.open_realtime(
            runtime,
            realtime_agent,
            build_agent(realtime_agent),
            bindings=RunBindings.embedded(),
            previous_state=saved.harness,
        ) as stream,
    ):
        assert stream.thread_id == saved.harness.thread_id
        assert "lookup_order" in provider.tools
        assert "My order number is 42." in str(provider.messages)
        await stream.send("Please keep the answer short.", respond=False)
        assert await connection.outgoing.get() == TextContext("Please keep the answer short.")
        await stream.send_audio(microphone)
        sent = await connection.outgoing.get()
        assert isinstance(sent, BinaryAudio) and sent.data == microphone

        await connection.incoming.put(
            ToolCall("call_order", tool_name="lookup_order", args='{"order_id":42}', response_usage_follows=True)
        )
        await connection.incoming.put(ResponseDone())
        result = await connection.outgoing.get()
        assert isinstance(result, ToolResult)
        assert result.tool_call_id == "call_order"
        assert "Order 42 has shipped." in str(result)

        async def playback() -> bytes:
            return b"".join([chunk async for chunk in stream.stream_audio()])

        async with asyncio.TaskGroup() as tasks:
            audio = tasks.create_task(playback())
            await connection.incoming.put(AudioDelta(speaker))
            await connection.incoming.put(OutputTranscript("Your order has shipped.", is_final=True))
            await connection.incoming.put(ResponseDone())
            async for item in stream:
                if isinstance(item, HarnessEvent) and isinstance(item.event, RealtimeTurnCompleteEvent):
                    await stream.close()
        assert audio.result() == speaker

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
