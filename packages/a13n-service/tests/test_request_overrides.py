"""Request overrides retain native wire precedence without crossing Provider authority."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

import httpx2
import pytest
from a13n_harness import AgentContext, HarnessState, RunBindings
from a13n_harness.metering import ModelUsageBinding
from a13n_harness.model_affinity import derive_model_affinity_id
from a13n_harness.models import SelfHealingModelCapability
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_harness.providers.model.apis import MODEL_APIS
from a13n_harness.providers.model.openai import DEFINITION
from a13n_harness.toolsets.file_media import MediaUnderstandingRequest
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.model_settings import check_settings, settings_schema
from a13n_service.providers.registry import Registry
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.resources.models.schemas import ModelConfig
from a13n_service.resources.models.service import ResolvedModel, model_settings
from a13n_service.runs.agent import ResolvedAgent, _settings, build, media_understanding, model_resolver
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("api", list(MODEL_APIS))
def test_override_schema_is_api_qualified(api: str) -> None:
    schema = settings_schema(api)
    assert "default" not in schema["properties"]["extra_headers"]
    assert check_settings(schema, {"extra_headers": {"X-Experiment": "on"}}, field="settings") == {
        "extra_headers": {"x-experiment": "on"}
    }
    raw = {"extra_body": {"future_option": {"model": "nested-data", "enabled": True}}}
    if api in {"openai.responses", "openai.chat_completions"}:
        assert check_settings(schema, raw, field="settings") == raw
        check_settings(schema, {"extra_body": {}}, field="settings")
    else:
        with pytest.raises(ServiceError):
            check_settings(schema, raw, field="settings")


@pytest.mark.parametrize("api", ["openai.responses", "openai.chat_completions"])
@pytest.mark.parametrize(
    "key",
    [
        "model",
        "messages",
        "input",
        "tools",
        "tool_choice",
        "functions",
        "function_call",
        "text",
        "response_format",
        "stream",
        "stream_options",
        "conversation",
        "previous_response_id",
        "prompt",
        "background",
        "store",
        "moderation",
        "web_search_options",
        "n",
    ],
)
def test_raw_body_cannot_replace_host_owned_fields(api: str, key: str) -> None:
    with pytest.raises(ServiceError) as refused:
        check_settings(settings_schema(api), {"extra_body": {key: None}}, field="model_settings")
    assert refused.value.details["field"] == "model_settings.extra_body"


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        "secret-value",
        {"x-ok": "line\nbreak"},
        {"X-One": "a", "x-one": "b"},
        {"Authorization": "secret-value"},
        {"Content-Type": "other"},
    ],
)
def test_invalid_headers_are_rejected_without_echoing_values(value: Any) -> None:
    with pytest.raises(ServiceError) as refused:
        check_settings(settings_schema("openai.responses"), {"extra_headers": value}, field="settings")
    assert "secret-value" not in str(refused.value)


def selected(config: ModelConfig | None = None, **provider: Any) -> ResolvedModel:
    return ResolvedModel(
        id=new_object_id("mdl"),
        key=new_object_id("mdl").replace("_", "-"),
        version=1,
        config=config or ModelConfig(model_name="future-model", model_api="openai.responses"),
        pricing=None,
        provider=cast(
            Any,
            SimpleNamespace(type="openai", config={"auth_mode": "none"}, credential=None, extra_headers={}, **provider),
        ),
    )


def test_provider_owned_headers_remain_reserved_and_defaults_replace_whole() -> None:
    registry = Registry.of([DEFINITION])
    model = selected(
        ModelConfig(
            model_name="future",
            model_api="openai.responses",
            extra_body={"reasoning": {"effort": "future"}},
            extra_headers={"x-default": "one"},
        )
    )
    assert model_settings(model.config, model.provider, {}, registry=registry, field="settings")["extra_body"] == {
        "reasoning": {"effort": "future"}
    }
    cleared = model_settings(
        model.config, model.provider, {"extra_body": {}, "extra_headers": {}}, registry=registry, field="settings"
    )
    assert cleared == {"openai_store": False, "extra_body": {}, "extra_headers": {}}
    provider = SimpleNamespace(
        type="openai",
        config={"auth_mode": "api_key", "api_key_header_name": "x-custom-key", "session_affinity_header": "x-thread"},
        credential=object(),
        extra_headers={"x-gateway-key": object()},
    )
    for name in ("X-Custom-Key", "X-Gateway-Key", "X-Thread"):
        with pytest.raises(ServiceError) as refused:
            model_settings(
                model.config, provider, {"extra_headers": {name: "secret-value"}}, registry=registry, field="settings"
            )
        assert refused.value.details["field"] == "settings.extra_headers"
        assert "secret-value" not in str(refused.value)


async def no_endpoint_check(*args: Any, **kwargs: Any) -> None:
    pass


@pytest.mark.parametrize("api", ["openai.responses", "openai.chat_completions"])
@pytest.mark.parametrize("stream", [False, True])
async def test_native_sdk_wire_preserves_raw_precedence(api: str, stream: bool) -> None:
    captured: list[dict] = []

    def request(request: httpx2.Request) -> httpx2.Response:
        captured.append(json.loads(request.content))
        assert request.headers["x-test"] == "on"
        return httpx2.Response(400, json={"error": {"message": "offline capture", "type": "invalid_request_error"}})

    raw = (
        {"reasoning": {"effort": "future-effort"}}
        if api == "openai.responses"
        else {"reasoning_effort": "future-effort"}
    )
    model = selected(ModelConfig(model_name="gpt-5", model_api=api))
    settings = model_settings(
        model.config,
        model.provider,
        {
            "thinking": False,
            "temperature": 0.1,
            "extra_body": {**raw, "temperature": 0.8, "vendor_extension": {"enabled": True}},
            "extra_headers": {"X-Test": "on"},
        },
        registry=Registry.of([DEFINITION]),
        field="settings",
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(request)) as client:
        native = await DEFINITION.build(
            "gpt-5",
            configuration={"base_url": "https://offline.invalid/v1", "auth_mode": "none"},
            model_api=api,
            http_client=client,
            endpoint_policy=SimpleNamespace(validate=no_endpoint_check),
        )
        async with native:
            with pytest.raises(ModelHTTPError):
                if stream:
                    async with native.request_stream(
                        [ModelRequest(parts=[UserPromptPart("hello")])], cast(Any, settings), ModelRequestParameters()
                    ) as response:
                        async for _ in response:
                            pass
                else:
                    await native.request(
                        [ModelRequest(parts=[UserPromptPart("hello")])], cast(Any, settings), ModelRequestParameters()
                    )
    assert len(captured) == 1
    body = captured[0]
    assert body["model"] == "gpt-5" and body["stream"] is stream
    if api == "openai.responses":
        assert body["store"] is False
    else:
        assert "store" not in body
    assert body["temperature"] == 0.8 and body["vendor_extension"] == {"enabled": True}
    assert all(body[key] == value for key, value in raw.items())


async def test_service_composition_binds_affinity_per_thread_without_mutating_client() -> None:
    requests: list[httpx2.Request] = []

    def request(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        chunks = [
            {
                "id": "chatcmpl-test",
                "created": 1,
                "model": "gpt-5",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}],
            },
            {
                "id": "chatcmpl-test",
                "created": 1,
                "model": "gpt-5",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        ]
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n",
        )

    configuration = {
        "base_url": "https://offline.invalid/v1",
        "auth_mode": "none",
        "session_affinity_header": "x-thread",
    }
    model = selected(
        ModelConfig(model_name="gpt-5", model_api="openai.chat_completions", extra_headers={"x-model": "default"})
    )
    model.provider.config = configuration
    root = ResolvedAgent(
        new_object_id("ap"),
        new_object_id("apr"),
        AgentConfig.model_validate({"model": model.key, "toolsets": {}}),
        model,
        None,
        {},
        {},
        {},
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(request)) as client:
        native = await DEFINITION.build(
            "gpt-5",
            configuration=configuration,
            model_api=model.config.model_api,
            http_client=client,
            endpoint_policy=SimpleNamespace(validate=no_endpoint_check),
        )
        async with native:
            executable = build(
                root, capabilities=lambda _: [], plugins=HarnessPluginFactoryCatalog([]), instrumentation=None
            )
            leaves = []
            executable._agent.root_capability.apply(leaves.append)
            assert sum(isinstance(capability, SelfHealingModelCapability) for capability in leaves) == 1
            bindings = RunBindings.embedded(model_resolver=model_resolver(root, {model.key: native}))

            async def run(thread: str) -> None:
                result = await executable.run(
                    "hello", previous_state=HarnessState.new(thread_id=thread), bindings=bindings
                )
                assert result.output_or_raise() == "ok"

            await asyncio.gather(run("thread_one"), run("thread_two"))
            await run("thread_one")
        assert "x-thread" not in native.client.default_headers
    assert sorted(request.headers["x-thread"] for request in requests) == sorted(
        [
            derive_model_affinity_id("thread_one"),
            derive_model_affinity_id("thread_two"),
            derive_model_affinity_id("thread_one"),
        ]
    )
    assert all(request.headers["x-model"] == "default" for request in requests)


async def test_media_affinity_uses_calling_thread_and_its_own_model_defaults() -> None:
    requests: list[httpx2.Request] = []

    def request(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-media",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-5",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "A landscape"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            },
        )

    primary = selected()
    media = selected(
        ModelConfig(
            model_name="gpt-5",
            model_api="openai.chat_completions",
            extra_body={"reasoning_effort": "future"},
            extra_headers={"x-media": "yes"},
        )
    )
    media.provider.config = {
        "base_url": "https://offline.invalid/v1",
        "auth_mode": "none",
        "session_affinity_header": "x-media-thread",
    }
    root = ResolvedAgent(
        new_object_id("ap"),
        new_object_id("apr"),
        AgentConfig.model_validate({"model": primary.key, "toolsets": {}}),
        primary,
        None,
        {"image": media},
        {},
        {},
    )

    async def primary_stream(messages: Any, info: Any) -> Any:
        if len(messages) == 1:
            yield {0: DeltaToolCall(name="inspect_media", json_args="{}", tool_call_id="media-tool")}
        else:
            yield "done"

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(request)) as client:
        native = await DEFINITION.build(
            "gpt-5",
            configuration=media.provider.config,
            model_api=media.config.model_api,
            http_client=client,
            endpoint_policy=SimpleNamespace(validate=no_endpoint_check),
        )
        async with native:
            provider = media_understanding(root, {media.key: native})

            async def inspect_media(ctx: RunContext[AgentContext]) -> str:
                result = await provider.understand(
                    MediaUnderstandingRequest(
                        kind="image", media_type="image/png", source_name="fixture.png", source_bytes=b"fixture"
                    ),
                    usage=ModelUsageBinding.for_context(
                        ctx.deps,
                        source="files.media_understanding",
                        tool_id="inspect_media",
                        tool_call_id=ctx.tool_call_id,
                    ),
                )
                return result.text

            executable = build(
                root,
                capabilities=lambda _: [Capability(id="tools", tools=[inspect_media])],
                plugins=HarnessPluginFactoryCatalog([]),
                instrumentation=None,
            )
            bindings = RunBindings.embedded(
                model_resolver=model_resolver(
                    root, {primary.key: FunctionModel(stream_function=primary_stream), media.key: native}
                )
            )

            async def run(thread: str) -> None:
                result = await executable.run(
                    "inspect", previous_state=HarnessState.new(thread_id=thread), bindings=bindings
                )
                assert result.output_or_raise() == "done"
                receipts = [record for record in result.usage_records if record.source == "files.media_understanding"]
                assert len(receipts) == 1 and receipts[0].model_name == "gpt-5"

            await asyncio.gather(run("thread_mediaone"), run("thread_mediatwo"))
        assert "x-media-thread" not in native.client.default_headers
    assert sorted(request.headers["x-media-thread"] for request in requests) == sorted(
        [
            derive_model_affinity_id("thread_mediaone"),
            derive_model_affinity_id("thread_mediatwo"),
        ]
    )
    assert all(request.headers["x-media"] == "yes" for request in requests)
    assert all(json.loads(request.content)["reasoning_effort"] == "future" for request in requests)


async def resources(service: Any, scripted: Any, kit: Any, *, settings: dict | None = None) -> tuple[dict, dict, dict]:
    provider = await service.client.post(
        f"{service.api}/model-providers",
        json={
            "type": "openai",
            "name": "Gateway",
            "config": {"base_url": scripted.url, "session_affinity_header": "x-thread"},
            "credential": {"api_key": "test-key"},
            "extra_headers": {"x-gateway-key": "private"},
        },
    )
    assert provider.status_code == 201, provider.text
    model = await service.client.post(
        f"{service.api}/models",
        json={
            "provider_id": provider.json()["id"],
            "key": "raw",
            "name": "Raw",
            "config": {
                "model_name": "scripted",
                "model_api": "openai.chat_completions",
                "extra_body": {"reasoning_effort": "model-default"},
                "extra_headers": {"x-model": "default"},
            },
        },
    )
    assert model.status_code == 201, model.text
    agent = await kit.add_agent(service, "raw-agent", model.json()["key"], model_settings=settings or {})
    return provider.json(), model.json(), agent


async def test_persisted_overrides_reach_wire_and_clear_defaults(
    executing: Any, scripted_model: Any, runs_kit: Any
) -> None:
    headers: list[dict] = []

    @scripted_model.app.middleware("http")
    async def capture(request: Any, call_next: Any) -> Any:
        headers.append(dict(request.headers))
        return await call_next(request)

    _, _, agent = await resources(
        executing,
        scripted_model,
        runs_kit,
        settings={"temperature": 0.2, "extra_body": {"reasoning_effort": "agent"}, "extra_headers": {"x-agent": "yes"}},
    )
    for overrides, effort, expected_headers in [
        ({}, "agent", {"x-agent": "yes"}),
        ({"model_settings": None}, "agent", {"x-agent": "yes"}),
        ({"model_settings": {}}, "model-default", {"x-model": "default"}),
        ({"model_settings": {"extra_body": {}, "extra_headers": {}}}, None, {}),
        (
            {"model_settings": {"extra_body": {"reasoning_effort": "future"}, "extra_headers": {"X-Run": "yes"}}},
            "future",
            {"x-run": "yes"},
        ),
    ]:
        options = {} if not overrides else {"options": {"overrides": overrides}}
        scripted_model.say("Done")
        started = await runs_kit.start_thread(executing, agent, "hi", **options)
        run = await runs_kit.sealed(executing, started["run"]["id"])
        assert run["status"] == "completed", run
        body = await scripted_model.requests.get()
        assert body.get("reasoning_effort") == effort
        wire = headers[-1]
        assert wire["x-thread"] == derive_model_affinity_id(run["thread_id"])
        assert wire["x-gateway-key"] == "private"
        assert {key: wire[key] for key in ("x-model", "x-agent", "x-run") if key in wire} == expected_headers
        if overrides.get("model_settings") is None:
            assert body["temperature"] == 0.2
        else:
            assert "temperature" not in body
            assert run["options"]["overrides"]["model_settings"] == overrides["model_settings"]
    assert len({entry["x-thread"] for entry in headers}) == 5


async def test_live_provider_change_rechecks_old_agent_before_request(
    executing: Any, scripted_model: Any, runs_kit: Any
) -> None:
    provider, _, agent = await resources(
        executing, scripted_model, runs_kit, settings={"extra_headers": {"X-New-Secret": "was-public"}}
    )
    updated = await executing.client.patch(
        f"{executing.api}/model-providers/{provider['id']}",
        json={"extra_headers": {"x-new-secret": "now-private"}},
        headers=runs_kit.if_match(provider),
    )
    assert updated.status_code == 200, updated.text
    started = await runs_kit.start_thread(executing, agent, "hi")
    run = await runs_kit.sealed(executing, started["run"]["id"])
    assert run["status"] == "failed", run
    assert scripted_model.requests.empty()


async def test_authoring_and_run_override_reject_provider_header_collisions(
    service: Any, scripted_model: Any, runs_kit: Any
) -> None:
    _, model, agent = await resources(service, scripted_model, runs_kit)
    for name in ("Authorization", "X-Gateway-Key", "X-Thread"):
        settings = {"extra_headers": {name: "do-not-echo"}}
        response = await service.client.post(
            f"{service.api}/agents/validate",
            json={"config": {"model": model["key"], "model_settings": settings}},
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"]["details"]["field"] == "model_settings.extra_headers"
        assert "do-not-echo" not in response.text
        response = await service.client.post(
            f"{service.api}/threads",
            json=runs_kit.message(agent, "hi", options={"overrides": {"model_settings": settings}}),
            headers=runs_kit.fresh_key(),
        )
        assert response.status_code == 400, response.text
        assert "do-not-echo" not in response.text


@pytest.mark.parametrize("mode", ["inline", "async"])
async def test_child_and_continuation_requests_keep_thread_affinity(
    service: Any, scripted_model: Any, runs_kit: Any, mode: str
) -> None:
    await runs_kit.pause_sweeps(service)
    captured: list[tuple[dict, str]] = []

    @scripted_model.app.middleware("http")
    async def capture(request: Any, call_next: Any) -> Any:
        captured.append((await request.json(), request.headers["x-thread"]))
        return await call_next(request)

    _, model, _ = await resources(service, scripted_model, runs_kit)
    worker = await runs_kit.add_agent(service, "worker", model["key"], instructions="Role: worker")
    coordinator = await runs_kit.add_agent(
        service,
        "coordinator",
        model["key"],
        instructions="Role: coordinator",
        subagent_mode=mode,
        subagents={"helper": {"agent_id": worker["id"], "description": "Computes answers"}},
    )
    scripted_model.call(
        "delegate",
        {"subagent" if mode == "inline" else "subagent_name": "helper", "prompt": "compute"},
        call_id="call_d",
        to="Role: coordinator",
    )
    scripted_model.say("42", to="Role: worker")
    scripted_model.say("Done", to="Role: coordinator")
    started = await runs_kit.start_thread(service, coordinator, "delegate")
    await (await runs_kit.attempt(service))
    if mode == "async":
        await (await runs_kit.attempt(service))
    root = await runs_kit.get_run(service, started["run"]["id"])
    assert root["status"] == "completed", root
    parent_id = derive_model_affinity_id(root["thread_id"])
    child_ids = {affinity for body, affinity in captured if "Role: worker" in json.dumps(body["messages"])}
    assert len(child_ids) == 1 and parent_id not in child_ids
    parent_ids = {affinity for body, affinity in captured if "Role: coordinator" in json.dumps(body["messages"])}
    assert parent_ids == {parent_id}

    # A new Run in this Thread keeps the same affinity; the child has not mutated its client's defaults.
    scripted_model.say("Again", to="Role: coordinator")
    continued = await runs_kit.submit(service, root["thread_id"], runs_kit.message(coordinator, "continue"))
    assert continued.status_code == 201, continued.text
    await (await runs_kit.attempt(service))
    assert captured[-1][1] == parent_id


@pytest.mark.parametrize("api", list(MODEL_APIS))
def test_service_defaults_are_scoped_to_responses(api: str) -> None:
    model = selected(ModelConfig(model_name="custom", model_api=api))
    expected = {"openai_store": False} if api == "openai.responses" else {}
    assert _settings(model, {}) == expected
    assert model.config.defaults() == {}  # The baseline is not an authored value.


@pytest.mark.parametrize("store", [True, False, None])
def test_native_model_defaults_and_call_overrides_share_composition(store: bool | None) -> None:
    model = selected(
        ModelConfig(
            model_name="gpt-5",
            model_api="openai.responses",
            max_tokens=1024,
            extra_headers={"x-legacy": "old"},
            settings={
                "max_tokens": 8192,
                "thinking": "medium",
                "openai_store": store,
                "openai_reasoning_summary": "detailed",
                "extra_headers": {},
            },
        )
    )
    registry = Registry.of([DEFINITION])
    assert _settings(model, {}) == model_settings(model.config, model.provider, {}, registry=registry, field="config")
    assert _settings(model, {})["openai_store"] is store
    assert _settings(model, {})["max_tokens"] == 8192
    assert _settings(model, {})["extra_headers"] == {}
    overrides = {"openai_store": False, "thinking": False, "extra_headers": {"X-Request": "yes"}}
    assert _settings(model, overrides) == model_settings(
        model.config, model.provider, overrides, registry=registry, field="settings"
    )
    assert _settings(model, overrides)["thinking"] is False
    assert model.config.settings["openai_store"] is store
    assert overrides["extra_headers"] == {"X-Request": "yes"}


@pytest.mark.parametrize("store", [True, False])
async def test_explicit_model_store_and_reasoning_defaults_reach_native_wire(store: bool) -> None:
    captured: list[dict] = []

    def request(request: httpx2.Request) -> httpx2.Response:
        captured.append(json.loads(request.content))
        return httpx2.Response(400, json={"error": {"message": "offline capture", "type": "invalid_request_error"}})

    model = selected(
        ModelConfig(
            model_name="gpt-5",
            model_api="openai.responses",
            settings={
                "openai_store": store,
                "thinking": "medium",
                "openai_reasoning_summary": "detailed",
                "max_tokens": 8192,
            },
        )
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(request)) as client:
        native = await DEFINITION.build(
            model.config.model_name,
            configuration={"base_url": "https://offline.invalid/v1", "auth_mode": "none"},
            model_api=model.config.model_api,
            http_client=client,
            endpoint_policy=SimpleNamespace(validate=no_endpoint_check),
        )
        async with native:
            with pytest.raises(ModelHTTPError):
                await native.request(
                    [ModelRequest(parts=[UserPromptPart("hello")])], _settings(model, {}), ModelRequestParameters()
                )
    assert captured[0]["store"] is store
    assert captured[0]["reasoning"] == {"effort": "medium", "summary": "detailed"}
    assert captured[0]["max_output_tokens"] == 8192
    assert "reasoning.encrypted_content" in captured[0]["include"]
