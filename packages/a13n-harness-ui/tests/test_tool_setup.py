from __future__ import annotations

import asyncio

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.configuration.models import ModelResource
from a13n_harness_ui.interactive.onboarding import LandingScreen, SetupBack, SetupCancelled, run_setup
from a13n_harness_ui.interactive.setup import SetupWizard
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.tool_presets import tool_choices
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput


def _wizard(provider="codex", model="gpt-5.6-sol"):
    wizard = SetupWizard()
    wizard.accept(provider)
    if provider == "api":
        wizard.accept("openai-responses")
        wizard.accept("")
        wizard.accept("new")
        wizard.accept("env:TEST_KEY")
    wizard.accept(model)
    while wizard.question.key != "tools":
        wizard.accept("")
    return wizard


@pytest.mark.parametrize(
    "provider,model,expected",
    [("codex", "gpt-5.6-sol", {"web_search", "image_generation"}), ("grok", "grok-4.6", {"web_search"})],
)
def test_subscription_multiselect_defaults_and_explicit_clear(provider, model, expected):
    wizard = _wizard(provider, model)
    selector = wizard.selection_prompt()
    assert selector.multiple
    assert {selector.choices[i].value for i in selector.checked} == expected
    assert "Host web is always included" in wizard.notice()
    selector.checked.clear()
    assert selector.answer() == "none"
    wizard.accept(selector.answer())
    wizard.accept("full-control")
    assert wizard.selection("/tmp")["tool_capabilities"] == [
        {"capability": "web", "configuration": {"search": {"mode": "host"}, "scrape": {"mode": "host"}}}
    ]


@pytest.mark.parametrize(
    "selected,search,scrape",
    [
        ((), "host", "host"),
        (("web_search",), "off", "host"),
        (("web_fetch",), "host", "off"),
        (("web_search", "web_fetch"), "off", "off"),
    ],
)
def test_native_web_operations_independently_replace_host_defaults(selected, search, scrape):
    from a13n_harness_ui.tool_presets import selected_tool_capabilities

    capabilities = selected_tool_capabilities(selected)
    assert capabilities[0] == {
        "capability": "web",
        "configuration": {"search": {"mode": search}, "scrape": {"mode": scrape}},
    }
    assert [cap["configuration"]["kind"] for cap in capabilities[1:]] == list(selected)


def test_backtracking_preserves_tools_but_model_change_resets_resource_inputs():
    wizard = _wizard("api", "gpt-5.4")
    wizard.accept("file_search,mcp_server")
    assert wizard.question.key == "file_stores"
    with pytest.raises(ValueError, match="store IDs"):
        wizard.accept("")
    wizard.accept("vs_one, vs_two")
    wizard.accept("https://mcp.example/mcp")
    wizard.accept("docs")
    wizard.back()
    wizard.back()
    wizard.back()
    wizard.back()
    assert wizard.question.key == "tools"
    assert wizard.question.default == "file_search,mcp_server"
    while wizard.question.key != "model":
        wizard.back()
    wizard.accept("gpt-5.5")
    assert not {"tools", "file_stores", "mcp_url", "mcp_id"} & wizard.values.keys()


def test_upstream_profiles_and_host_prerequisites_limit_candidates():
    def keys(route):
        return {choice.key for choice in tool_choices(route, authentication="api_key")}

    assert "advisor" in keys("anthropic:claude-sonnet-4-6")
    assert "advisor" not in keys("anthropic:claude-sonnet-4-5")
    assert "memory" not in keys("anthropic:claude-sonnet-4-6")
    assert "x_search" in keys("xai:grok-4.6")
    assert keys("grok:grok-4.6") == set()
    assert keys("google:gemini-2.5-pro") == set()
    assert "file_search" in keys("google:gemini-3.1-pro-preview")
    assert [choice.key for choice in tool_choices("openai-responses:custom") if choice.recommended] == []


@pytest.mark.parametrize(
    "route,module",
    [
        ("openai-chat:gpt-5", None),
        ("custom:unknown", None),
        ("openai-responses:gpt-5", "openai"),
        ("anthropic:claude-sonnet-4-6", "anthropic"),
        ("google:gemini-3.1-pro-preview", "google"),
        ("openrouter:openai/gpt-5", "openrouter"),
        ("xai:grok-4.6", "xai"),
    ],
)
def test_native_choices_import_only_the_selected_adapter(route, module, monkeypatch):
    import builtins

    from a13n_harness_ui import tool_presets

    original_import = builtins.__import__
    imported = []

    def observe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if globals is tool_presets.__dict__ and name.startswith("pydantic_ai.models."):
            imported.append(name)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", observe_import)
    choices = tool_choices(route, authentication="api_key")
    assert imported == ([] if module is None else [f"pydantic_ai.models.{module}"])
    if module is None:
        assert choices == ()
    else:
        assert choices


def test_google_file_search_requires_deselecting_other_native_tools():
    model = ModelResource.model_validate(
        {
            "schema_version": "1",
            "kind": "model",
            "id": "model-google",
            "name": "Gemini",
            "route": "google:gemini-3.1-pro-preview",
            "authentication": {"kind": "api_key", "env": "TEST_KEY"},
        }
    )
    wizard = SetupWizard(add_agent=True, model_resources={model.id: model})
    wizard.values["model_source"] = model.id
    while wizard.question.key != "tools":
        wizard.index += 1
    with pytest.raises(ValueError, match="cannot combine"):
        wizard.accept("file_search,web_search")
    wizard.accept("file_search")
    wizard.accept("fileSearchStores/test")
    wizard.accept("Search agent")
    selections = wizard.selection("/tmp")["tool_capabilities"]
    assert selections[0]["capability"] == "web"
    assert selections[1]["configuration"]["file_store_ids"] == ["fileSearchStores/test"]


@pytest.mark.anyio
async def test_landing_keyboard_toggle_and_clear_preserve_empty_selection():
    wizard = _wizard()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        async with LandingScreen() as screen:
            task = asyncio.create_task(screen.ask(wizard.question, wizard.selection_prompt()))
            await asyncio.sleep(0)
            # Clear both checked choices, then confirm. Space must toggle,
            # not enter a blank answer that re-applies the recommended defaults.
            pipe.send_text(" \x1b[B \r")
            assert await asyncio.wait_for(task, 2) == "none"


@pytest.mark.anyio
@pytest.mark.parametrize("followup", ["agent", "back", "cancel"])
async def test_add_model_handoff_publishes_resources_and_native_tools_on_new_agent(tmp_path, followup):
    path = tmp_path / "config.yaml"
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    phase = "initial"
    asked = []

    async def ask(question, selection):
        asked.append((phase, question.key))
        if question.key == "provider":
            return "api"
        if question.key == "api_provider":
            return "xai" if phase == "model" else "openai-responses"
        if question.key == "credential":
            return "env:TEST_KEY"
        if phase == "model" and question.key == "action":
            if followup == "back":
                raise SetupBack()
            if followup == "cancel":
                raise SetupCancelled()
            return "agent"
        if phase == "model" and question.key == "tools":
            assert "x_search" in question.choices
            return "web_search,x_search,file_search,mcp_server"
        if question.key == "file_stores":
            return "collection-docs"
        if question.key == "mcp_url":
            return "https://mcp.example/mcp"
        if question.key == "mcp_id":
            return "docs"
        return question.default

    async with open_harness_ui_app(settings, configuration_path=path) as app:
        assert await run_setup(app, tmp_path, ask_user=ask, emit=lambda _: None)
        baseline = {p: p.read_bytes() for p in tmp_path.rglob("*.yaml")}
        phase = "model"
        assert await run_setup(app, tmp_path, add_model=True, ask_user=ask, emit=lambda _: None)
        config = await app.current_configuration()
        model = next(model for model in config.models.values() if model.route.startswith("xai:"))
        assert all(p.read_bytes() == content for p, content in baseline.items())
        assert model.model_configuration == {}
        if followup != "agent":
            assert len(config.agents) == 1
            assert ("model", "tools") not in asked
            return
        agent = next(agent for agent in config.agents.values() if agent.model == model.id)
        assert "tool_capabilities" not in model.model_dump()
        native = {
            cap.configuration["kind"]: cap.configuration for cap in agent.capabilities if cap.capability == "NativeTool"
        }
        assert set(native) == {"web_search", "x_search", "file_search", "mcp_server"}
        assert native["file_search"]["file_store_ids"] == ["collection-docs"]
        assert native["mcp_server"]["url"] == "https://mcp.example/mcp"
        assert ("model", "base_url") not in asked
        assert all(p.read_bytes() == content for p, content in baseline.items())


@pytest.mark.parametrize(
    "route,search,image",
    [
        ("openai-responses:gpt-4.1", True, True),
        ("openai-responses:gpt-6-astra", True, True),
        ("openai-responses:gateway-model-alias", True, True),
        ("openai-chat:gpt-4.1", False, False),
        ("anthropic:claude-sonnet-4-6", True, False),
        ("google:gemini-3.1-pro-preview", True, False),
        ("google:gemini-2.5-pro", False, False),
        ("google:gemini-3-pro-image-preview", False, False),
        ("openrouter:openai/gpt-5.4", True, False),
        ("xai:grok-4.6", True, False),
        ("grok:grok-4.6", False, False),
    ],
)
@pytest.mark.parametrize("endpoint", [None, "https://gateway.example/v1"])
def test_api_native_choices_follow_adapter_not_endpoint_or_model_suggestion_list(route, search, image, endpoint):
    choices = tool_choices(route, authentication="api_key", base_url=endpoint)
    keys = {choice.key for choice in choices}
    assert ("web_search" in keys) is search
    assert ("image_generation" in keys) is image
    if endpoint is not None:
        assert not any(choice.recommended for choice in choices)


@pytest.mark.parametrize("mode", ["setup", "agent", "model"])
@pytest.mark.parametrize("advanced", [False, True])
@pytest.mark.parametrize("provider", ["codex", "grok", "openai-responses", "openai-chat", "xai"])
def test_wizard_progress_counts_only_visible_steps(mode, advanced, provider):
    wizard = SetupWizard(add_agent=mode == "agent", add_model=mode == "model", advanced=advanced)
    visited = []
    totals = []
    while wizard.question is not None:
        question = wizard.question
        current, total = wizard.progress
        assert current == len(visited) + 1
        assert current <= total
        assert wizard.notice().startswith(f"{current} / {total} · ")
        visited.append(question.key)
        totals.append(total)
        answer = question.default
        if question.key == "provider":
            answer = provider if provider in {"codex", "grok"} else "api"
        elif question.key == "api_provider":
            answer = provider
        elif question.key == "credential":
            answer = "env:TEST_KEY"
        wizard.accept(answer)
    assert wizard.progress == (len(visited), len(visited))
    assert totals[-1] == len(visited)
    assert ("tools" in visited) is (mode != "model" and provider != "openai-chat")
    if provider == "xai":
        assert "base_url" not in visited
    for index, key in reversed(list(enumerate(visited))):
        assert wizard.back()
        assert wizard.question.key == key
        assert wizard.progress[0] == index + 1
    assert not wizard.back()


def test_resource_steps_change_progress_when_selected_and_removed():
    wizard = _wizard("api", "gpt-4.1")
    current, total = wizard.progress
    wizard.accept("file_search,mcp_server")
    assert wizard.progress == (current + 1, total + 3)
    for key, answer in [("file_stores", "vs_docs"), ("mcp_url", "https://mcp.example"), ("mcp_id", "docs")]:
        assert wizard.question.key == key
        wizard.accept(answer)
    assert wizard.progress[0] == wizard.progress[1]
    for _ in range(4):
        assert wizard.back()
    assert wizard.question.key == "tools"
    wizard.accept("none")
    assert wizard.question.key == "environment"
    assert wizard.progress == (total, total)


@pytest.mark.anyio
async def test_custom_api_setup_and_existing_model_agent_publish_selected_search_and_saved_images(tmp_path):
    from a13n_harness_ui.interactive.selection import Choice

    path = tmp_path / "config.yaml"
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    asked = []

    async def ask(question, selection):
        asked.append(question.key)
        answers = {
            "provider": "api",
            "api_provider": "openai-responses",
            "base_url": "https://gateway.example/v1",
            "credential": "env:TEST_KEY",
            "model": "gateway-model-alias",
            "tools": "web_search,image_generation",
        }
        if question.key == "tools":
            assert not selection.checked
        return answers.get(question.key, question.default)

    async with open_harness_ui_app(settings, configuration_path=path) as app:
        assert await run_setup(app, tmp_path, ask_user=ask, emit=lambda _: None)
        configuration = await app.current_configuration()
        model = configuration.models["model-api-key"]
        original = {file: file.read_bytes() for file in tmp_path.rglob("*.yaml")}
        wizard = SetupWizard(
            add_agent=True,
            model_choices=(Choice(model.id, model.name),),
            model_resources={model.id: model},
        )
        wizard.accept(model.id)
        assert wizard.question.key == "tools"
        assert wizard.progress == (2, 3)
        asked.clear()
        assert await run_setup(
            app, tmp_path, add_agent=True, existing_model_id=model.id, ask_user=ask, emit=lambda _: None
        )
        assert asked == ["model_source", "tools", "name"]
        configuration = await app.current_configuration()
        for agent in configuration.agents.values():
            caps = {cap.capability: cap.configuration for cap in agent.capabilities}
            assert caps["web"] == {"search": {"mode": "off"}, "scrape": {"mode": "host"}}
            assert caps["NativeTool"]["kind"] == "web_search"
            assert caps["native_image_generation"] == {}
        assert all(file.read_bytes() == content for file, content in original.items())
