import json
from collections import deque
from pathlib import Path

import pytest
import yaml
from a13n_harness.spec import HarnessModelCharacteristics, ModelCapability
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition import AgentCompositionResolver
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.configuration.setup import SetupSelection, preview_setup, publish_setup
from a13n_harness_ui.interactive.onboarding import run_setup
from a13n_harness_ui.interactive.setup import SetupWizard
from a13n_harness_ui.model_presets import known_model_capabilities
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings

IMAGE = frozenset({ModelCapability.IMAGE_UNDERSTANDING})


@pytest.mark.parametrize(
    "route,expected",
    [
        ("openai-responses:gpt-6-astra", IMAGE),
        ("openai-chat:gpt-5.4", IMAGE),
        ("openai-codex:gpt-5.6-sol", IMAGE),
        ("anthropic:claude-sonnet-4-6", IMAGE),
        ("grok:grok-4.6", IMAGE),
        ("moonshotai:kimi-k2.5", IMAGE),
        ("google:gemini-2.5-pro", frozenset(ModelCapability)),
        ("openrouter:google/gemini-2.5-pro", IMAGE),
        ("openrouter:anthropic/claude-sonnet-4.6", IMAGE),
        ("openrouter:openai/gpt-5.4", IMAGE),
        ("openrouter:openai/gpt-6-astra", IMAGE),
        ("openrouter:x-ai/grok-4.6", IMAGE),
        ("zai:glm-4.7", frozenset()),
        ("deepseek:deepseek-v4-flash-vision-exp", IMAGE),
        ("openai-chat:custom-gpt-5.4", None),
        ("openai-chat:GPT-5.4", None),
        ("openai-chat:claude-sonnet-4-6", None),
        ("openrouter:custom/gpt-5.4", None),
        ("openrouter:openai/gpt-5.4:free", None),
        ("openrouter:openai/custom-gpt-5.4", None),
    ],
)
def test_known_media_uses_exact_catalog_identity_and_transport(route, expected) -> None:
    assert known_model_capabilities(route) == expected


def test_context_only_catalog_entry_is_not_a_text_only_claim(monkeypatch) -> None:
    from a13n_harness import model_catalog

    entry = model_catalog.OfficialModelEntry(
        model="openai:context-only",
        characteristics=HarnessModelCharacteristics(context_window=123456),
        source_url="https://example.com/model",
    )
    monkeypatch.setattr(
        model_catalog, "get_official_model_catalog", lambda: model_catalog.OfficialModelCatalog({entry.key: entry})
    )
    assert known_model_capabilities(entry.key) is None


@pytest.mark.anyio
@pytest.mark.parametrize("capabilities", [None, [], ["audio_understanding"]])
async def test_app_setup_fills_only_omitted_capabilities(tmp_path: Path, capabilities) -> None:
    characteristics = {
        "context_window": 123456,
        "proactive_context_management_threshold": None,
        "compact_threshold": 0.8,
    }
    if capabilities is not None:
        characteristics["capabilities"] = capabilities
    selection = SetupSelection.model_validate_json(
        json.dumps(
            {
                "api_key_model": {
                    "route": "anthropic:claude-sonnet-4-6",
                    "authentication": {"kind": "api_key", "env": "TEST_KEY"},
                    "model_characteristics": characteristics,
                    "model_configuration": {"base_url": "https://proxy.example/v1"},
                },
                "project": "project-local",
                "project_path": str(tmp_path),
                "default_agent": "agent-api-key",
                "environment_profile": "environment-native",
            }
        )
    )
    path = tmp_path / "config.yaml"
    validate = AgentCompositionResolver().validate_generation
    preview = await preview_setup(path, selection, validate_candidate=validate)
    expected = ["image_understanding"] if capabilities is None else capabilities
    document = yaml.safe_load(preview.files["models/api-key.yaml"])
    assert document["model_characteristics"] == {**characteristics, "capabilities": expected}
    publication = await publish_setup(path, selection, validate_candidate=validate)
    assert publication.completed
    assert (tmp_path / "models/api-key.yaml").read_bytes() == preview.files["models/api-key.yaml"].encode("utf-8")
    assert (await load_harness_ui_configuration(path)).models[
        "model-api-key"
    ].model_characteristics.capabilities == frozenset(expected)


@pytest.mark.anyio
async def test_programmatic_setup_without_characteristics_seeds_stable_media_list(tmp_path: Path) -> None:
    selection = SetupSelection.model_validate(
        {
            "api_key_model": {
                "route": "google:gemini-2.5-pro",
                "authentication": {"kind": "api_key", "env": "TEST_KEY"},
            },
            "default_agent": "agent-api-key",
            "project": "project-local",
            "project_path": str(tmp_path),
            "environment_profile": "environment-native",
        }
    )
    validate = AgentCompositionResolver().validate_generation
    preview = await preview_setup(tmp_path / "config.yaml", selection, validate_candidate=validate)
    characteristics = yaml.safe_load(preview.files["models/api-key.yaml"])["model_characteristics"]
    assert characteristics == {
        "capabilities": ["audio_understanding", "image_understanding", "video_understanding"],
        "context_window": 350000,
        "proactive_context_management_threshold": 0.65,
        "compact_threshold": 0.90,
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mode,chosen_name",
    [("setup", ""), ("add_model", ""), ("add_agent", ""), ("add_model", "研究助手"), ("add_agent", "研究助手")],
)
@pytest.mark.parametrize("provider", ["api", "codex", "grok"])
async def test_all_creation_flows_persist_known_native_image_support(
    tmp_path: Path, mode, chosen_name, provider
) -> None:
    connection = {
        "api": ["api", "anthropic", "", "env:TEST_KEY", "claude-sonnet-4-6", "", ""],
        "codex": ["codex", "later", "gpt-5.6-sol", ""],
        "grok": ["grok", "later", "grok-4.6"],
    }[provider]
    answers = deque([*connection, "full-control"])
    notices = []

    async def ask(question, selection):
        assert answers, question
        if question.key == "name":
            assert question.default.isascii() and question.default.isprintable()
            assert " - " in question.default
        return answers.popleft()

    path = tmp_path / "config.yaml"
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        assert await run_setup(app, tmp_path, ask_user=ask, emit=notices.append)
        assert not answers
        baseline = {p: p.read_bytes() for p in tmp_path.rglob("*.yaml")}
        if mode != "setup":
            answers.extend([*(["new"] if mode == "add_agent" else []), *connection, chosen_name])
            assert await run_setup(
                app,
                tmp_path,
                ask_user=ask,
                emit=notices.append,
                add_model=mode == "add_model",
                add_agent=mode == "add_agent",
            )
            assert not answers
            assert all(p.read_bytes() == content for p, content in baseline.items())
        source = await app.current_configuration()
        assert source.models
        names = [
            resource.name for resource in (*source.models.values(), *source.agents.values(), *source.projects.values())
        ]
        assert all(name.isascii() and name.isprintable() for name in names if name != chosen_name)
        if chosen_name:
            assert chosen_name in names
        # Includes generated subscription shell-review models, not just the coding model.
        assert all(model.model_characteristics.capabilities == IMAGE for model in source.models.values())
        assert any("Native media input: image." in notice for notice in notices)


def test_wizard_backtracking_recomputes_media_notice_without_stale_hints() -> None:
    wizard = SetupWizard()
    for answer in ("api", "anthropic", "", "env:TEST_KEY", "claude-sonnet-4-6", "", ""):
        wizard.accept(answer)
    assert "Native media input: image." in wizard.notice()
    while wizard.question.key != "model":
        assert wizard.back()
    wizard.accept("custom-model")
    wizard.accept("")
    wizard.accept("")
    assert "Native media input: unknown; no native media enabled." in wizard.notice()
    while wizard.question.key != "api_provider":
        assert wizard.back()
    for answer in ("google", "https://proxy.example/v1", "env:TEST_KEY", "gemini-2.5-pro", "", ""):
        wizard.accept(answer)
    assert "Native media input: audio, image, video." in wizard.notice()
