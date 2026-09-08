from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import cast

import pytest
from a13n_harness import AgentContext
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition.models import ResolvedModelRecipe
from a13n_harness_ui.configuration import ApiKeyAuthentication
from a13n_harness_ui.errors import CompositionError
from a13n_harness_ui.interactive.onboarding import SetupCancelled, run_setup
from a13n_harness_ui.interactive.setup import SetupWizard
from a13n_harness_ui.model_adapters import PydanticAiModelAdapter
from a13n_harness_ui.model_presets import API_PROVIDERS, settings_presets
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from pydantic_ai.models import ModelResolutionContext
from pydantic_ai.providers import infer_provider_class


@pytest.mark.anyio
@pytest.mark.parametrize("provider", API_PROVIDERS, ids=lambda provider: provider.route)
async def test_every_offered_provider_constructs_native_model_with_selected_endpoint(provider, monkeypatch) -> None:
    from a13n_harness_ui import model_runtime

    monkeypatch.setenv("TEST_PROVIDER_KEY", "fixture-key")
    endpoint = "https://example.invalid/custom/v1"
    captured = []

    def infer(name):
        native = infer_provider_class(name)

        def build(**kwargs):
            captured.append(kwargs)
            return native(**kwargs)

        return build

    monkeypatch.setattr(model_runtime, "infer_provider_class", infer)
    model_name = "openai/test-model" if provider.route == "openrouter" else "test-model"
    recipe = ResolvedModelRecipe(
        model_id="model-test",
        route=f"{provider.route}:{model_name}",
        authentication=ApiKeyAuthentication(kind="api_key", env="TEST_PROVIDER_KEY"),
        model_configuration={"base_url": endpoint},
    )
    adapter = PydanticAiModelAdapter().validate(route=recipe.route, settings={}, model_cfg=recipe.model_configuration)
    assert adapter.model_cfg == {"base_url": endpoint}
    model = await HarnessUiModelResolver({recipe.model_id: recipe})(
        cast(ModelResolutionContext[AgentContext], None), recipe.model_id
    )
    assert model.model_name == model_name
    if provider.route == "grok":
        from pydantic_ai.models.openai import OpenAIChatModel

        assert isinstance(model, OpenAIChatModel)
        assert str(model.client.base_url).rstrip("/") == endpoint
    elif provider.transport == "openai-client":
        assert str(captured[0]["openai_client"].base_url).rstrip("/") == endpoint
    else:
        assert captured == [{"api_key": "fixture-key", "base_url": endpoint}]


@pytest.mark.parametrize("provider", API_PROVIDERS, ids=lambda provider: provider.route)
@pytest.mark.parametrize("model_id", ["claude-sonnet-4-5", "claude-sonnet-4-6"])
def test_all_presets_expand_to_accepted_native_settings(provider, model_id) -> None:
    for preset in settings_presets(provider.route, model_id):
        normalized = PydanticAiModelAdapter().validate(
            route=f"{provider.route}:{'anthropic/' if provider.route == 'openrouter' else ''}{model_id}",
            settings=preset.settings,
            model_cfg={},
        )
        assert normalized.settings == preset.settings


@pytest.mark.parametrize("model_id", ["gpt-4.1", "custom-model"])
def test_non_reasoning_or_unknown_models_have_a_neutral_default(model_id) -> None:
    presets = settings_presets("openai-responses", model_id)
    assert presets[0].key == "default"
    assert presets[0].settings == {"openai_store": False}
    assert all("openai_reasoning_summary" not in preset.settings for preset in presets)


def test_reasoning_defaults_and_anthropic_model_profile_selection() -> None:
    assert settings_presets("openai-responses", "gpt-5")[0].settings == {
        "thinking": "high",
        "openai_reasoning_summary": "detailed",
        "openai_store": False,
    }
    assert "openai_reasoning_summary" not in settings_presets("openai-chat", "custom")[0].settings
    assert settings_presets("anthropic", "claude-sonnet-4-6")[0].key == "adaptive"
    legacy = settings_presets("anthropic", "claude-sonnet-4-5")[0]
    assert legacy.key == "interleaved"
    assert legacy.settings["anthropic_thinking"]["display"] == "summarized"
    assert legacy.settings["anthropic_betas"] == ["interleaved-thinking-2025-05-14"]
    # Pydantic AI maps this to include_thoughts=True AND the model's effort;
    # a partial google_thinking_config would override that native translation.
    assert settings_presets("google", "gemini-2.5-pro")[0].settings == {"thinking": "high"}


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",
        "https://key@example.com",
        "https://example.com?api_key=secret",
        "https://example.com/#secret",
        "https://example.com:invalid",
        "not a url",
    ],
)
def test_base_url_rejects_credentials_and_invalid_endpoints(url) -> None:
    with pytest.raises(CompositionError):
        PydanticAiModelAdapter().validate(route="anthropic:claude", settings={}, model_cfg={"base_url": url})


def test_provider_specific_settings_cannot_leak_to_another_provider() -> None:
    with pytest.raises(CompositionError, match="settings"):
        PydanticAiModelAdapter().validate(
            route="openai-responses:gpt-5", settings={"anthropic_thinking": {"type": "adaptive"}}, model_cfg={}
        )
    with pytest.raises(CompositionError, match="settings"):
        PydanticAiModelAdapter().validate(
            route="anthropic:claude",
            settings={"max_tokens": 1024, "anthropic_thinking": {"type": "enabled", "budget_tokens": 1024}},
            model_cfg={},
        )


def test_api_wizard_backtracking_drops_incompatible_settings_and_endpoints() -> None:
    wizard = SetupWizard(add_agent=True, existing_agent_ids=frozenset({"agent-coding", "agent-coding-2"}))
    for answer in (
        "new",
        "api",
        "anthropic",
        "https://example.com",
        "env:TEST_KEY",
        "claude-sonnet-4-5",
        "interleaved",
        "200k",
        "Coding",
    ):
        wizard.accept(answer)
    assert wizard.selection("/tmp")["new_agent_id"] == "agent-coding-3"
    while wizard.question is None or wizard.question.key != "api_provider":
        assert wizard.back()
    wizard.accept("openai-responses")
    assert wizard.values == {"model_source": "new", "provider": "api", "api_provider": "openai-responses"}
    assert wizard.question.default == "https://api.openai.com/v1"


@pytest.mark.anyio
async def test_api_setup_then_repeated_add_never_replaces_agents_or_defaults(tmp_path: Path) -> None:
    answers = deque(
        ["api", "openai-responses", "https://example.com/v1", "fixture-secret", "gpt-5", "high", "", "full-control"]
    )
    questions, output = [], []

    async def ask(question, selection):
        questions.append(question)
        assert answers, question
        return answers.popleft()

    path = tmp_path / "config.yaml"
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        assert await run_setup(app, tmp_path, ask_user=ask, emit=output.append)
        original = {p: p.read_bytes() for p in tmp_path.rglob("*.yaml")}
        for endpoint in ("https://example.net/v1", "https://example.org/v1"):
            answers.extend(
                ["new", "api", "openai-responses", endpoint, "env:TEST_KEY", "gpt-5", "low", "128k", "Coding"]
            )
            assert await run_setup(app, tmp_path, ask_user=ask, emit=output.append, add_agent=True)
        source = await app.current_configuration()
        assert set(source.agents) == {"agent-api-key", "agent-coding", "agent-coding-2"}
        assert (
            source.models[source.agents["agent-coding"].model].model_configuration["base_url"]
            == "https://example.net/v1"
        )
        assert (
            source.models[source.agents["agent-coding-2"].model].model_configuration["base_url"]
            == "https://example.org/v1"
        )
        assert all(p.read_bytes() == content for p, content in original.items())
        assert source.document.defaults.agent == "agent-api-key"
        assert len(await app.list_api_keys()) == 1
    assert all(q.password for q in questions if q.key == "credential")
    assert "fixture-secret" not in "\n".join(output)
    assert all(b"fixture-secret" not in p.read_bytes() for p in tmp_path.rglob("*.yaml"))
    assert "openai_reasoning_summary" in "\n".join(output)


@pytest.mark.anyio
async def test_cancel_after_hidden_key_save_does_not_publish_configuration(tmp_path: Path) -> None:
    answers = deque(["api", "anthropic", "https://api.anthropic.com", "fixture-key"])

    async def ask(question, selection):
        if not answers:
            raise SetupCancelled()
        return answers.popleft()

    path = tmp_path / "config.yaml"
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        assert not await run_setup(app, tmp_path, ask_user=ask, emit=lambda text: None)
        assert len(await app.list_api_keys()) == 1
    assert not path.exists()
    assert not (tmp_path / "models").exists()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider,model_id",
    [
        ("openai-responses", "gpt-5"),
        ("openai-responses", "gpt-4.1"),
        ("anthropic", "claude-sonnet-4-5"),
        ("anthropic", "claude-sonnet-4-6"),
    ],
)
async def test_presets_reach_native_http_and_preserve_returned_thinking(provider, model_id, monkeypatch) -> None:
    import json

    import httpx2 as httpx
    from a13n_harness_ui import model_runtime
    from pydantic_ai import Agent
    from pydantic_ai.messages import ThinkingPart
    from pydantic_ai.settings import ModelSettings

    requests = []

    def respond(request):
        requests.append(request)
        if provider == "anthropic":
            body = {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": model_id,
                "content": [
                    {"type": "thinking", "thinking": "Provider summary", "signature": "fixture"},
                    {"type": "text", "text": "Done"},
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 2},
            }
        else:
            body = {
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "model": model_id,
                "status": "completed",
                "output": [
                    {
                        "id": "rs_test",
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": "Provider summary"}],
                    },
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "Done", "annotations": []}],
                    },
                ],
                "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            }
        if model_id == "gpt-4.1":
            body["output"] = body["output"][1:]
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:

        def infer(name):
            native = infer_provider_class(name)
            return lambda **kwargs: native(**kwargs, http_client=client)

        monkeypatch.setattr(model_runtime, "infer_provider_class", infer)
        monkeypatch.setenv("TEST_PROVIDER_KEY", "fixture-key")
        preset = settings_presets(provider, model_id)[0]
        recipe = ResolvedModelRecipe(
            model_id="model-test",
            route=f"{provider}:{model_id}",
            authentication=ApiKeyAuthentication(kind="api_key", env="TEST_PROVIDER_KEY"),
            settings=preset.settings,
            model_configuration={"base_url": "https://provider.invalid/api"},
        )
        model = await HarnessUiModelResolver({recipe.model_id: recipe})(
            cast(ModelResolutionContext[AgentContext], None), recipe.model_id
        )
        result = await Agent(model, model_settings=cast(ModelSettings, recipe.settings)).run("Hello")
    assert result.output == "Done"
    assert any(
        isinstance(part, ThinkingPart) and part.content == "Provider summary"
        for message in result.all_messages()
        for part in message.parts
    ) is (model_id != "gpt-4.1")
    assert len(requests) == 1
    assert str(requests[0].url).startswith("https://provider.invalid/api/")
    payload = json.loads(requests[0].content)
    if provider == "openai-responses":
        if model_id == "gpt-4.1":
            assert "reasoning" not in payload
        else:
            assert payload["reasoning"] == {"effort": "high", "summary": "detailed"}
        assert payload["store"] is False
    else:
        assert payload["thinking"]["display"] == "summarized"
        assert payload["max_tokens"] == 16384
        if preset.key == "interleaved":
            assert payload["thinking"]["budget_tokens"] == 8192
            assert "interleaved-thinking-2025-05-14" in requests[0].headers["anthropic-beta"]
        else:
            assert payload["thinking"]["type"] == "adaptive"
            assert payload["output_config"]["effort"] == "high"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider,model_id",
    [
        ("deepseek", "deepseek-reasoner"),
        ("deepseek", "deepseek-v4-pro"),
        ("zai", "glm-4.7"),
        ("zai", "glm-5.3"),
        ("moonshotai", "kimi-k2.5"),
        ("moonshotai", "kimi-k2-thinking"),
    ],
)
async def test_native_thinking_stream_tool_continuation_and_checkpoint_replay(provider, model_id, monkeypatch) -> None:
    """Real SDK parsing/serialization with mock HTTP; not live provider acceptance."""
    import json

    import httpx2 as httpx
    from a13n_harness_ui import model_runtime
    from openai import AsyncOpenAI
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessagesTypeAdapter, PartDeltaEvent, ThinkingPart, ThinkingPartDelta
    from pydantic_ai.models.zai import ZaiModel
    from pydantic_ai.run import AgentRunResultEvent
    from pydantic_ai.settings import ModelSettings

    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        first = len(payloads) == 1
        deltas = [
            {"role": "assistant", "reasoning_content": "plan " if first else "checked "},
            {"reasoning_content": "next" if first else "result"},
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_fixture",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }
                ]
            }
            if first
            else {"content": "Done"},
        ]
        chunks = [
            {
                "id": f"chat_{len(payloads)}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": model_id,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            for delta in deltas
        ]
        chunks.append(
            {
                "id": f"chat_{len(payloads)}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": model_id,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if first else "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        )
        content = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=content, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:

        def infer(name):
            native = infer_provider_class(name)

            def build(**kwargs):
                original = kwargs.pop("openai_client")
                return native(
                    openai_client=AsyncOpenAI(api_key="fixture-key", base_url=original.base_url, http_client=client),
                    **kwargs,
                )

            return build

        monkeypatch.setattr(model_runtime, "infer_provider_class", infer)
        monkeypatch.setenv("TEST_PROVIDER_KEY", "fixture-key")
        preset = settings_presets(provider, model_id)[0]
        assert preset.key == "thinking"
        normalized = PydanticAiModelAdapter().validate(
            route=f"{provider}:{model_id}", settings=preset.settings, model_cfg={}
        )
        recipe = ResolvedModelRecipe(
            model_id="model-test",
            route=f"{provider}:{model_id}",
            authentication=ApiKeyAuthentication(kind="api_key", env="TEST_PROVIDER_KEY"),
            settings=normalized.settings,
            model_configuration={"base_url": "https://provider.invalid/v1"},
        )
        # Exercise the same durable recipe representation as frozen reconstruction.
        recipe = ResolvedModelRecipe.model_validate_json(recipe.model_dump_json())
        model = await HarnessUiModelResolver({recipe.model_id: recipe})(
            cast(ModelResolutionContext[AgentContext], None), recipe.model_id
        )
        assert isinstance(model, ZaiModel) is (provider == "zai")
        agent = Agent(model, model_settings=cast(ModelSettings, recipe.settings))

        @agent.tool_plain
        def lookup() -> str:
            return "tool result"

        async with agent.run_stream_events("Use lookup") as stream:
            events = [event async for event in stream]
        assert any(
            isinstance(event, PartDeltaEvent)
            and isinstance(event.delta, ThinkingPartDelta)
            and event.delta.content_delta == "next"
            for event in events
        )
        assert isinstance(events[-1], AgentRunResultEvent)
        result = events[-1].result
        assert result.output == "Done"
        history = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(result.all_messages()))
        thoughts = [part.content for message in history for part in message.parts if isinstance(part, ThinkingPart)]
        assert thoughts == ["plan next", "checked result"]
        async with agent.run_stream_events("Continue", message_history=history) as stream:
            next_events = [event async for event in stream]
        assert isinstance(next_events[-1], AgentRunResultEvent)
        assert next_events[-1].result.output == "Done"

    assert len(payloads) == 3
    continuation = payloads[1]["messages"]
    assert next(message for message in continuation if message.get("tool_calls"))["reasoning_content"] == "plan next"
    assert any(message["role"] == "tool" and message["content"] == "tool result" for message in continuation)
    replayed = [message["reasoning_content"] for message in payloads[2]["messages"] if message["role"] == "assistant"]
    assert replayed == ["plan next", "checked result"]
    for payload in payloads:
        assert payload["stream"] is True
        assert "openai_reasoning_summary" not in payload
        assert payload.get("reasoning_effort") != "none"
        if provider == "zai":
            assert payload["thinking"] == {"type": "enabled", "clear_thinking": False}


def test_native_vendor_defaults_do_not_offer_fake_disable_or_generic_summary() -> None:
    for provider, model_id in (("deepseek", "deepseek-chat"), ("moonshotai", "moonshot-v1-8k"), ("zai", "glm-4")):
        assert settings_presets(provider, model_id)[0].settings == {}
    with pytest.raises(CompositionError):
        PydanticAiModelAdapter().validate(
            route="deepseek:deepseek-reasoner", settings={"zai_clear_thinking": False}, model_cfg={}
        )


@pytest.mark.parametrize("provider", API_PROVIDERS, ids=lambda provider: provider.route)
def test_provider_model_suggestions_accept_numeric_default_and_custom_case(provider) -> None:
    from a13n_harness_ui.model_presets import API_MODEL_SUGGESTIONS

    wizard = SetupWizard()
    for value in ("api", provider.route, "", "env:TEST_KEY"):
        wizard.accept(value)
    assert wizard.question.choices == API_MODEL_SUGGESTIONS[provider.route]
    wizard.accept("2")
    assert wizard.values["model"] == API_MODEL_SUGGESTIONS[provider.route][1]
    assert wizard.back()
    wizard.accept("Qwen/My-Custom-Model")
    assert wizard.values["model"] == "Qwen/My-Custom-Model"
    assert wizard.back()
    assert wizard.selection_prompt().cursor == 0
    wizard.accept("")
    assert wizard.values["model"] == "Qwen/My-Custom-Model"


@pytest.mark.parametrize("hint,expected", [(None, 350000), (1000000, 350000), (128000, 128000)])
def test_api_context_defaults_manual_override_and_model_change(hint, expected) -> None:
    wizard = SetupWizard()
    for value in ("api", "openai-chat", "", "env:TEST_KEY", "custom-model", "default"):
        wizard.accept(value)
    wizard.context_window_hint = hint
    assert wizard.question.key == "context"
    assert wizard.question.default == str(expected)
    assert "65%" in wizard.question.text and "90%" in wizard.question.text
    assert ("unknown" in wizard.question.text) is (hint is None)
    wizard.accept("")
    assert wizard.values["context"] == str(expected)
    assert wizard.back()
    for invalid in ("0", "-1", "unknown", "1.2k"):
        with pytest.raises(ValueError, match="positive"):
            wizard.accept(invalid)
    wizard.accept("100k")
    wizard.accept("full-control")
    characteristics = wizard.selection("/tmp")["api_key_model"]["model_characteristics"]
    assert characteristics == {
        "context_window": 100000,
        "proactive_context_management_threshold": 0.65,
        "compact_threshold": 0.90,
    }
    while wizard.question is None or wizard.question.key != "model":
        assert wizard.back()
    wizard.accept("another-custom-model")
    assert wizard.context_window_hint is None
    assert "context" not in wizard.values and "preset" not in wizard.values


def test_bundled_context_catalog_and_programmatic_default_use_harness_owner() -> None:
    from a13n_harness_ui.configuration.setup import SetupApiKeyModel
    from a13n_harness_ui.model_presets import known_context_window

    assert known_context_window("anthropic", "claude-haiku-4-5", "https://api.anthropic.com") == 200000
    assert known_context_window("openai-chat", "gpt-5.6-sol", "https://api.openai.com/v1") == 1050000
    assert known_context_window("openai-chat", "not-a-known-model", "https://localhost:8000") is None
    selection = SetupApiKeyModel(
        route="openai-chat:custom", authentication=ApiKeyAuthentication(kind="api_key", env="TEST_KEY")
    )
    assert selection.model_characteristics.context_window == 350000
    assert selection.model_characteristics.summary_reminder_tokens == 227500
    assert selection.model_characteristics.compact_threshold == 0.90


@pytest.mark.parametrize(
    "provider,model_id,title",
    [
        ("codex", "gpt-5.6-sol", "Codex - GPT-5.6 Sol"),
        ("deepseek", "deepseek-v4-pro", "DeepSeek V4 Pro"),
        ("anthropic", "claude-sonnet-4-6", "Anthropic - Claude Sonnet 4.6"),
        ("moonshotai", "kimi-k2.6", "Moonshot AI - Kimi K2.6"),
        ("zai", "glm-5.3", "Z.AI - GLM 5.3"),
        ("openai-chat", "Qwen/CustomID", "OpenAI Chat - Qwen/CustomID"),
    ],
)
def test_resource_display_names_preserve_identity(provider, model_id, title) -> None:
    from a13n_harness_ui.resource_names import model_name

    assert model_name(provider, model_id) == title


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider,model_id,window,title",
    [
        ("moonshotai", "kimi-k2.5", 262144, "Moonshot AI - Kimi K2.5"),
        ("openai-chat", "CustomModel", 350000, "OpenAI Chat - CustomModel"),
    ],
)
async def test_setup_context_and_names_survive_publication_capture_and_reconstruction(
    tmp_path, provider, model_id, window, title
) -> None:
    from a13n_harness_ui.composition import AgentCompositionResolver, AgentReconstructor, ThreadCompositionSelection
    from a13n_harness_ui.composition.models import ResolvedRunComposition

    # Advanced only to omit children; all connection/context defaults remain ordinary setup defaults.
    answers = deque(["api", provider, "", "env:TEST_KEY", model_id, "", "none", "", "", "full-control"])

    async def ask(question, selection):
        assert answers, question
        return answers.popleft()

    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=tmp_path / "config.yaml",
    ) as app:
        assert await run_setup(app, tmp_path, ask_user=ask, emit=lambda text: None, advanced=True)
        source = await app.current_configuration()
        assert source.models["model-api-key"].name == title
        assert source.agents["agent-api-key"].name == f"{title} - Coding"
        assert f"name: {title}\n" in (tmp_path / "models/api-key.yaml").read_text(encoding="utf-8")
        assert f"name: {title} - Coding\n" in (tmp_path / "agents/api-key.yaml").read_text(encoding="utf-8")
        characteristics = source.models["model-api-key"].model_characteristics
        assert characteristics.context_window == window
        expected_capabilities = frozenset({"image_understanding"}) if model_id == "kimi-k2.5" else frozenset()
        assert characteristics.capabilities == expected_capabilities
        composition = AgentCompositionResolver().resolve_run(
            source,
            ThreadCompositionSelection(
                thread_id="thread-test",
                version=1,
                project_id="project-local",
                agent_source_kind="agent",
                agent_source_id="agent-api-key",
                environment_profile_id="environment-native",
                harness_plugin_ids=(),
                environment_run_extension_ids=(),
                mcp_server_ids=(),
            ),
        )
        restored = ResolvedRunComposition.model_validate_json(composition.model_dump_json())
        reconstructed = AgentReconstructor().reconstruct(restored, subagent_operator=None)
        native = reconstructed.executable.definition.agent.model_characteristics
        assert native == characteristics
        assert native.summary_reminder_tokens == int(window * 0.65)
        assert native.compact_threshold == 0.90
    assert not answers
