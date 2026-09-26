from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import a13n_harness.builder as builder_module
import httpx2
import pytest
from a13n_harness import (
    AgentDefinition,
    AgentSpec,
    DefinitionError,
    HarnessBuilder,
    HarnessState,
    RunBindings,
    SubagentDefinition,
)
from a13n_harness.model_affinity import derive_model_affinity_id
from a13n_harness.models import (
    MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV,
)
from openai import AsyncOpenAI
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model, ModelResolutionContext
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.settings import ModelSettings

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("thread_id", "expected"),
    [
        ("thread-example", "c9b5a8bc-0a44-5dc0-836d-6f0af4c8e71d"),
        ("thread a/b\r\n\u4f1a\u8bdd", "3ec0222d-0e01-5754-a45f-c5a2bde6e6de"),
        ("c9b5a8bc-0a44-5dc0-836d-6f0af4c8e71d", "151e4eb1-13ab-5a6b-a7ab-8930dd4cc9d5"),
    ],
)
def test_affinity_id_has_stable_uuid_v5_wire_format(thread_id: str, expected: str) -> None:
    actual = derive_model_affinity_id(thread_id)
    assert actual == expected == derive_model_affinity_id(thread_id)
    assert len(actual) == 36
    assert str(UUID(actual)) == actual
    assert UUID(actual).version == 5
    assert actual != thread_id


def test_affinity_derivation_does_not_normalize_or_truncate_thread_ids() -> None:
    ids = ["thread-a", "thread_a", "Thread-a", "thread-a ", "a" * 10_000, "a" * 10_000 + "b"]
    assert len({derive_model_affinity_id(thread_id) for thread_id in ids}) == len(ids)


@pytest.mark.parametrize(
    ("model_name", "expected"),
    # One name per rule edge: suffix, prefix, non-digit, non-GPT, anchoring, case, and Unicode digit.
    [
        ("gpt-4.1", True),
        ("openai/gpt-5", True),
        ("gpt-oss-120b", False),
        ("o3", False),
        ("other/gpt-5", False),
        ("GPT-5", False),
        ("gpt-\uff15", False),
    ],
)
async def test_cache_key_uses_final_model_name(
    monkeypatch: pytest.MonkeyPatch, model_name: str, expected: bool
) -> None:
    await _assert_cache_key(monkeypatch, model_name, expected, "concrete")


@pytest.mark.parametrize("selection", ["resolved", "inferred"])
async def test_cache_key_uses_the_model_every_selection_path_resolves(
    monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    await _assert_cache_key(monkeypatch, "gpt-5", True, selection)


async def _assert_cache_key(monkeypatch: pytest.MonkeyPatch, model_name: str, expected: bool, selection: str) -> None:
    """The model name that decides the cache key is the one the run finally executes."""
    seen: list[ModelSettings | None] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        seen.append(info.model_settings)
        yield "ok"

    model = WrapperModel(FunctionModel(stream_function=stream, model_name=model_name))

    async def resolve(context: ModelResolutionContext, model_id: str) -> Model:
        assert model_id == "logical:primary"
        return model

    def infer(model_id: object, **kwargs: object) -> Model:
        assert model_id == "logical:primary"
        return model

    monkeypatch.setattr(builder_module, "infer_model", infer)
    executable = HarnessBuilder().build(
        AgentSpec(model=None if selection == "concrete" else "logical:primary"),
        model=model if selection == "concrete" else None,
        output_type=str,
    )
    result = await executable.run(
        "hello", bindings=RunBindings.embedded(model_resolver=resolve if selection == "resolved" else None)
    )
    assert result.output_or_raise() == "ok"
    assert result.state is not None
    settings = ModelSettings()
    if expected:
        settings["openai_prompt_cache_key"] = derive_model_affinity_id(result.state.thread_id)
    assert seen == [settings]


# Both on merges both patches; both off takes the early return. Mixed values add no branch.
@pytest.mark.parametrize(("session_enabled", "cache_enabled"), [(True, True), (False, False)])
async def test_builder_overrides_environment_for_parent_and_child(
    monkeypatch: pytest.MonkeyPatch, session_enabled: bool, cache_enabled: bool
) -> None:
    # Explicit values must not even parse the corresponding ambient value.
    monkeypatch.setenv("A13N_HARNESS_MODEL_REQUEST_X_SESSION_ID_ENABLED", "invalid")
    monkeypatch.setenv(MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV, "invalid")
    seen: list[ModelSettings | None] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        seen.append(info.model_settings)
        yield "ok"

    model = FunctionModel(stream_function=stream, model_name="gpt-5")
    builder = HarnessBuilder(
        session_affinity_header="x-session-id" if session_enabled else None,
        openai_prompt_cache_key_enabled=cache_enabled,
    )
    monkeypatch.setenv("A13N_HARNESS_MODEL_REQUEST_X_SESSION_ID_ENABLED", str(not session_enabled))
    monkeypatch.setenv(MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV, str(not cache_enabled))
    executable = builder.build(
        AgentSpec(),
        model=model,
        output_type=str,
        subagents=(
            SubagentDefinition(
                name="child",
                description="Child",
                agent=AgentDefinition(agent=AgentSpec(), model=model, output_type=str),
            ),
        ),
    )
    for agent in (executable, executable.subagents["child"].executable):
        result = await agent.run("hello", bindings=RunBindings.embedded())
        assert result.output_or_raise() == "ok"
        assert result.state is not None
        expected = ModelSettings()
        if session_enabled:
            expected["extra_headers"] = {"x-session-id": derive_model_affinity_id(result.state.thread_id)}
        if cache_enabled:
            expected["openai_prompt_cache_key"] = derive_model_affinity_id(result.state.thread_id)
        assert seen[-1] == (expected or None)


@pytest.mark.parametrize("override", ["session_affinity_header", "openai_prompt_cache_key_enabled"])
async def test_unspecified_builder_switch_still_follows_environment(
    monkeypatch: pytest.MonkeyPatch, override: str
) -> None:
    monkeypatch.setenv("A13N_HARNESS_MODEL_REQUEST_X_SESSION_ID_ENABLED", "false")
    monkeypatch.setenv(MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV, "false")
    seen: list[ModelSettings | None] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        seen.append(info.model_settings)
        yield "ok"

    executable = HarnessBuilder(**{override: "x-session-id" if override == "session_affinity_header" else True}).build(
        AgentSpec(), model=FunctionModel(stream_function=stream, model_name="gpt-5"), output_type=str
    )
    result = await executable.run("hello", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "ok"
    assert result.state is not None
    expected = ModelSettings()
    if override == "session_affinity_header":
        expected["extra_headers"] = {"x-session-id": derive_model_affinity_id(result.state.thread_id)}
    else:
        expected["openai_prompt_cache_key"] = derive_model_affinity_id(result.state.thread_id)
    assert seen == [expected]


@pytest.mark.parametrize(("parameter", "value"), [("openai_prompt_cache_key_enabled", "false")])
def test_builder_rejects_non_boolean_patch_override(parameter: str, value: Any) -> None:
    with pytest.raises(DefinitionError) as exc_info:
        HarnessBuilder(**{parameter: value})
    assert exc_info.value.code == "model_request_patch_configuration_invalid"
    assert exc_info.value.details == {"name": parameter}


@pytest.mark.parametrize(
    ("model_name", "enabled"), [("gpt-5", True), ("gpt-5", False), ("deepseek/deepseek-chat", True)]
)
async def test_explicit_affinity_survives_name_filter_and_disabled_patches(model_name: str, enabled: bool) -> None:
    seen: list[ModelSettings | None] = []
    settings = ModelSettings(
        openai_prompt_cache_key="explicit-cache", extra_headers={"X-Session-ID": "explicit-session"}
    )

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        seen.append(info.model_settings)
        yield "ok"

    executable = HarnessBuilder(
        session_affinity_header="x-session-id" if enabled else None, openai_prompt_cache_key_enabled=enabled
    ).build(
        AgentSpec(model_settings=settings),
        model=FunctionModel(stream_function=stream, model_name=model_name),
        output_type=str,
    )
    result = await executable.run("hello", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "ok"
    assert seen == [settings]
    assert settings == ModelSettings(
        openai_prompt_cache_key="explicit-cache", extra_headers={"X-Session-ID": "explicit-session"}
    )


async def test_gpt_cache_affinity_survives_continuation_and_changes_on_fork() -> None:
    seen: list[ModelSettings | None] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        seen.append(info.model_settings)
        yield "ok"

    def build():
        return HarnessBuilder(session_affinity_header="x-session-id").build(
            AgentSpec(), model=FunctionModel(stream_function=stream, model_name="gpt-5-codex"), output_type=str
        )

    executable = build()
    state = HarnessState.new(thread_id="thr_hostroot")
    first = await executable.run("hello", bindings=RunBindings.embedded(), previous_state=state)
    assert first.state is not None
    restored = HarnessState.model_validate_json(first.state.model_dump_json())
    second = await build().run("continue", bindings=RunBindings.embedded(), previous_state=restored)
    fork = await executable.run("branch", bindings=RunBindings.embedded(), previous_state=first.state.fork())
    assert second.state is not None and fork.state is not None
    assert first.output_or_raise() == second.output_or_raise() == fork.output_or_raise() == "ok"
    assert first.state.thread_id == second.state.thread_id == state.thread_id
    assert fork.state.thread_id != state.thread_id
    assert all(
        settings["extra_headers"]["x-session-id"] == settings["openai_prompt_cache_key"]
        for settings in seen
        if settings
    )
    assert [settings["openai_prompt_cache_key"] for settings in seen if settings] == [
        derive_model_affinity_id(first.state.thread_id),
        derive_model_affinity_id(second.state.thread_id),
        derive_model_affinity_id(fork.state.thread_id),
    ]


@pytest.mark.parametrize(
    ("model_name", "expect_cache_key"),
    [("deepseek/deepseek-chat", False), ("openai/gpt-5", True)],
)
async def test_openrouter_request_body_only_gets_automatic_cache_key_for_gpt(
    model_name: str, expect_cache_key: bool
) -> None:
    bodies: list[dict[str, Any]] = []
    headers: list[httpx2.Headers] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        headers.append(request.headers)
        chunk = {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model_name,
            "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
        }
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        model = OpenRouterModel(
            model_name,
            provider=OpenRouterProvider(
                openai_client=AsyncOpenAI(api_key="test-key", base_url="https://example.test/v1", http_client=client)
            ),
        )
        executable = HarnessBuilder(session_affinity_header="x-litellm-session-id").build(
            AgentSpec(), model=model, output_type=str
        )
        result = await executable.run("hello", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "ok"
    assert result.state is not None
    assert len(bodies) == 1
    assert "x-session-id" not in headers[0]
    assert headers[0]["x-litellm-session-id"] == derive_model_affinity_id(result.state.thread_id)
    if expect_cache_key:
        assert bodies[0]["prompt_cache_key"] == derive_model_affinity_id(result.state.thread_id)
    else:
        assert "prompt_cache_key" not in bodies[0]


@pytest.mark.parametrize("header", [None, "X-Custom-Affinity"])
async def test_opt_in_affinity_is_thread_scoped_across_continuation_child_and_fork(header) -> None:
    seen = []

    async def stream(messages, info):
        seen.append(dict(info.model_settings or {}))
        yield "ok"

    model = FunctionModel(stream_function=stream, model_name="example-model")
    executable = HarnessBuilder(session_affinity_header=header).build(
        AgentSpec(),
        model=model,
        output_type=str,
        subagents=(
            SubagentDefinition(
                name="child",
                description="Child",
                agent=AgentDefinition(agent=AgentSpec(), model=model, output_type=str),
            ),
        ),
    )
    first = await executable.run("hello", bindings=RunBindings.embedded())
    assert first.state is not None
    restored = HarnessState.model_validate_json(first.state.model_dump_json())
    second = await executable.run("continue", bindings=RunBindings.embedded(), previous_state=restored)
    fork = await executable.run("fork", bindings=RunBindings.embedded(), previous_state=first.state.fork())
    child = await executable.subagents["child"].executable.run("child", bindings=RunBindings.embedded())
    states = [first.state, second.state, fork.state, child.state]
    ids = [state.thread_id for state in states if state is not None]
    assert len(ids) == 4 and ids[0] == ids[1] and len(set(ids)) == 3
    assert seen == [
        ({"extra_headers": {header.lower(): derive_model_affinity_id(thread_id)}} if header else {})
        for thread_id in ids
    ]


@pytest.mark.parametrize("header", ["bad\r\nheader", "a" * 129, "authorization", "Host", 1])
def test_affinity_header_rejects_invalid_or_owned_names(header) -> None:
    with pytest.raises(DefinitionError):
        HarnessBuilder(session_affinity_header=header)


def test_affinity_ignores_removed_legacy_environment(monkeypatch) -> None:
    from a13n_harness.models.request_headers import ModelRequestPatchConfiguration

    monkeypatch.setenv("A13N_HARNESS_MODEL_REQUEST_X_SESSION_ID_ENABLED", "true")
    configuration = ModelRequestPatchConfiguration.from_environment(session_affinity_header="X-Custom")
    assert configuration.session_affinity_header == "x-custom"
    assert ModelRequestPatchConfiguration.from_environment().session_affinity_header is None


def test_custom_header_does_not_consult_legacy_environment_but_validates_host_arguments(monkeypatch):
    monkeypatch.setenv("A13N_HARNESS_MODEL_REQUEST_X_SESSION_ID_ENABLED", "invalid")
    HarnessBuilder(session_affinity_header="x-custom")
    with pytest.raises(TypeError):
        HarnessBuilder(session_affinity_header="x-custom", x_session_id_enabled="yes")
