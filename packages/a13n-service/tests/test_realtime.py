"""Exercise realtime execution with a Service-composed Agent and a persisted Thread checkpoint."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace

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
from a13n_service.runs.tables import RunRow
from a13n_service.settings import Providers
from pydantic import SecretStr
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import BinaryAudio, ModelMessage, RealtimeTurnCompleteEvent
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.realtime import RealtimeModelSettings
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

pytestmark = pytest.mark.anyio


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
