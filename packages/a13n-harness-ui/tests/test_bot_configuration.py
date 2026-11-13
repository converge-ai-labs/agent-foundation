from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BOT_THREAD_ID, BotSubmit
from a13n_harness_ui.composition import ThreadCompositionSelection
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from anyio import Event, fail_after, sleep
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from .test_app import _settings
from .test_bot import _write_configuration, consumed, ready

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize("explicit_agent", [False, True])
async def test_bot_inherits_selected_execution_policy_but_replaces_conversational_role(
    tmp_path: Path, override: bool, explicit_agent: bool
):
    root = _write_configuration(tmp_path, instructions="Ordinary Agent role must not leak")
    if override:
        (tmp_path / "models/alternate.yaml").write_text(
            (tmp_path / "models/primary.yaml").read_text().replace("model-primary", "model-alternate")
        )
        root.write_text(root.read_text() + "    model: model-alternate\n")
    root.write_text(root.read_text() + "    instructions: Custom Bot policy\n")
    agent_path = tmp_path / "agents/assistant.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["capabilities"] = [
        {"capability": "compaction", "configuration": {"trigger_tokens": 12345}},
        {"capability": "handoff", "configuration": {}},
        {"capability": "runtime_context", "configuration": {"context_window_tokens": 42000}},
        {"capability": "dynamic_environment", "configuration": {"shell_enabled": True, "files_enabled": True}},
        {"capability": "ToolPermissionsCapability", "configuration": {"rules": {"create_project": "ask"}}},
    ]
    agent["subagents"] = [{"agent": "agent-child"}]
    if explicit_agent:
        agent["id"] = "agent-coordinator"
        agent_path = tmp_path / "agents/coordinator.yaml"
        root.write_text(root.read_text() + "    agent: agent-coordinator\n")
    agent_path.write_text(yaml.safe_dump(agent))
    (tmp_path / "agents/child.yaml").write_text(
        'schema_version: "1"\nkind: agent\nid: agent-child\nname: Child\nmodel: model-primary\ninstructions: Child policy\n'
    )
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        thread = await app._threads.bot_thread()
        source = await app.current_configuration()
        composition = app._mcp_app_owners.resolver.resolve_run(source, ThreadCompositionSelection.from_thread(thread))
        node = composition.root
        assert node.source_kind == "bot"
        assert node.base_agent_id == ("agent-coordinator" if explicit_agent else "agent-assistant")
        assert node.model.model_id == ("model-alternate" if override else "model-primary")
        assert node.instructions == ("Custom Bot policy",)
        assert "Ordinary Agent role" not in str(node.system_prompt)
        assert "Drive working Threads" in str(node.system_prompt)
        capabilities = {item.capability: item.configuration for item in node.capabilities}
        assert capabilities["compaction"]["trigger_tokens"] == 12345
        assert capabilities["runtime_context"]["context_window_tokens"] == 42000
        assert "handoff" in capabilities and "user_interaction" not in capabilities
        assert capabilities["ToolPermissionsCapability"]["rules"] == {"create_project": "ask"}
        assert capabilities["dynamic_environment"] == {
            "files_enabled": True,
            "shell_enabled": True,
            "computer_enabled": True,
        }
        assert node.children[0].definition.instructions == ("Child policy",)
        inspection = await app.inspect_thread_configuration(BOT_THREAD_ID)
        assert inspection.next_model_id == node.model.model_id
        assert set(inspection.next_capability_ids) == set(capabilities)
        assert inspection.captured is None


async def test_bot_compacts_automatically_with_selected_agent_and_model_override(tmp_path: Path, monkeypatch):
    root = _write_configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    selected = tmp_path / "agents/coordinator.yaml"
    selected.write_text(
        agent.read_text().replace("agent-assistant", "agent-coordinator")
        + "capabilities:\n  - capability: compaction\n    configuration:\n      trigger_tokens: 10\n"
    )
    root.write_text(root.read_text() + "    agent: agent-coordinator\n    model: model-primary\n")
    model = tmp_path / "models/primary.yaml"
    model.write_text(model.read_text() + "model_characteristics: {context_window_tokens: 10000}\n")
    started, release = Event(), Event()
    requests: list[str] = []
    summaries = 0

    async def foreground(messages, info):
        requests.append(str(messages))
        yield "obsolete Bot history " * 30 if len(requests) == 1 else "Bot answer"

    async def background(messages, info):
        nonlocal summaries
        summaries += 1
        started.set()
        await release.wait()
        return ModelResponse(
            parts=[TextPart("retained Bot summary")], usage=RequestUsage(input_tokens=100, output_tokens=10)
        )

    async def resolve(self, model_id, *, thread_id):
        return FunctionModel(function=background, stream_function=foreground)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        for index, text in enumerate(("first input", "second input")):
            await bot.submit(BotSubmit(input_id=f"compact-{index}", text=text))
            assert (await consumed(app, f"compact-{index}")).state == "completed"
        with fail_after(10):
            await started.wait()
        release.set()
        with fail_after(10):
            while (result := await app._store.compactions.get(BOT_THREAD_ID)) is None or result.status == "running":
                await sleep(0.01)
        assert result.status == "ready", result
        await bot.submit(BotSubmit(input_id="compact-current", text="current input"))
        assert (await consumed(app, "compact-current")).state == "completed"
        assert "retained Bot summary" in requests[-1]
        assert "obsolete Bot history" not in requests[-1]
        assert "current input" in requests[-1]
        assert summaries == 1


@pytest.mark.parametrize("capacity", [42000, None])
async def test_bot_context_uses_active_capture_then_selected_continuation_not_next_model(
    tmp_path: Path,
    monkeypatch,
    capacity: int | None,
):
    root = _write_configuration(tmp_path)
    model_path = tmp_path / "models/primary.yaml"
    model = yaml.safe_load(model_path.read_text())
    model["route"] = "openai:fictional-model"
    if capacity is not None:
        model["model_characteristics"] = {"context_window_tokens": capacity}
    model_path.write_text(yaml.safe_dump(model))
    started, release = Event(), Event()

    async def stream(messages, info):
        started.set()
        await release.wait()
        yield "Captured answer"

    async def resolve(self, model_id, *, thread_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await app._threads.bot_thread()
        assert (await app.context_usage(BOT_THREAD_ID)).context_window is None
        await bot.submit(BotSubmit(input_id="meter-first", text="First request"))
        with fail_after(10):
            await started.wait()
        active = await app.context_usage(BOT_THREAD_ID)
        assert active.model_id == "model-primary"
        assert active.context_window == capacity
        assert active.latest_request_tokens is None
        assert (await app.inspect_thread_configuration(BOT_THREAD_ID)).capture_source == "active_operation"
        release.set()
        assert (await consumed(app, "meter-first")).state == "completed"
        idle = await app.context_usage(BOT_THREAD_ID)
        assert idle.context_window == capacity
        assert idle.latest_request_tokens is not None
        assert idle.latest_request_tokens == idle.input_tokens + idle.output_tokens
        # Accepted future policy must not relabel the already captured Run.
        model["model_characteristics"] = {"context_window_tokens": 90000}
        model_path.write_text(yaml.safe_dump(model))
        await app.reload_configuration()
        assert (await app.context_usage(BOT_THREAD_ID)).context_window == capacity
        second_started, second_release = Event(), Event()
        started, release = second_started, second_release
        await bot.submit(BotSubmit(input_id="meter-second", text="Next request"))
        with fail_after(10):
            await started.wait()
        current = await app.context_usage(BOT_THREAD_ID)
        assert current.context_window == 90000
        assert current.latest_request_tokens == idle.latest_request_tokens
        assert current.record_id == idle.record_id
        assert current.context_breakdown == idle.context_breakdown
        release.set()
        assert (await consumed(app, "meter-second")).state == "completed"
        updated = await app.context_usage(BOT_THREAD_ID)
        assert updated.context_window == 90000
        assert updated.record_id is not None and updated.record_id != current.record_id


async def test_bot_retains_execution_allowlist_and_removes_only_blocking_questions(tmp_path: Path, monkeypatch):
    root = _write_configuration(tmp_path)
    agent_path = tmp_path / "agents/assistant.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["tools"] = ["ask_user_question", "shell_exec", "shell_info", "computer_click", "list_threads"]
    agent_path.write_text(yaml.safe_dump(agent))
    observed = []

    async def stream(messages, info):
        observed.extend(tool.name for tool in info.function_tools)
        yield "Bot is available"

    async def resolve(self, model_id, *, thread_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await bot.submit(BotSubmit(input_id="allowlist", text="Coordinate"))
        assert (await consumed(app, "allowlist")).state == "completed"
        assert "list_threads" in observed
        assert {"shell_exec", "shell_info"} <= set(observed)
        assert not ({"ask_user_question", "computer_click"} & set(observed))
        agent["tool_search"] = {
            "groups": {"questions": {"description": "Questions", "capabilities": ["user_interaction"]}}
        }
        agent_path.write_text(yaml.safe_dump(agent))
        source = await app.reload_configuration()
        thread = await app._threads.get(BOT_THREAD_ID)
        node = app._mcp_app_owners.resolver.resolve_agent(source, ThreadCompositionSelection.from_thread(thread))
        assert node.tool_search is not None
        assert all("user_interaction" not in group.capabilities for group in node.tool_search.groups.values())
        assert node.tools == ("shell_exec", "shell_info", "computer_click", "list_threads")


@pytest.mark.parametrize("default_agent", [False, True])
async def test_explicit_bot_model_does_not_require_a_default_agent_model(tmp_path: Path, default_agent: bool):
    root = _write_configuration(tmp_path)
    document = yaml.safe_load(root.read_text())
    document["webui"]["bot"]["model"] = "model-primary"
    if not default_agent:
        document["defaults"].pop("agent")
    else:
        agent_path = tmp_path / "agents/assistant.yaml"
        agent = yaml.safe_load(agent_path.read_text())
        agent.pop("model")
        agent_path.write_text(yaml.safe_dump(agent))
    root.write_text(yaml.safe_dump(document))
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        thread = await app._threads.bot_thread()
        source = await app.current_configuration()
        node = app._mcp_app_owners.resolver.resolve_agent(source, ThreadCompositionSelection.from_thread(thread))
        assert node.model.model_id == "model-primary"
        assert node.base_agent_id == ("agent-assistant" if default_agent else None)
        # Legacy model-only configurations load unchanged. Old captures remain
        # readable and do not acquire provenance from today's default Agent.
        legacy = node.model_dump(mode="json")
        legacy.pop("base_agent_id", None)
        restored = type(node).model_validate(legacy)
        assert restored.base_agent_id is None
        assert restored.model_dump(mode="json") == legacy
        assert "user_interaction" not in {item.capability for item in node.capabilities}


@pytest.mark.parametrize("agent_id", [None, "agent-coordinator"])
@pytest.mark.parametrize("model_id", [None, "model-primary"])
async def test_bot_agent_model_selection_matrix_and_default_changes(tmp_path: Path, agent_id, model_id):
    root = _write_configuration(tmp_path)
    (tmp_path / "models/alternate.yaml").write_text(
        (tmp_path / "models/primary.yaml").read_text().replace("model-primary", "model-alternate")
    )
    (tmp_path / "agents/coordinator.yaml").write_text(
        (tmp_path / "agents/assistant.yaml")
        .read_text()
        .replace("agent-assistant", "agent-coordinator")
        .replace("model-primary", "model-alternate")
    )
    document = yaml.safe_load(root.read_text())
    document["webui"]["bot"].update(agent=agent_id, model=model_id)
    root.write_text(yaml.safe_dump(document))
    original = root.read_bytes()
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        thread = await app._threads.bot_thread()
        source = await app.current_configuration()
        composition = app._mcp_app_owners.resolver.resolve_run(source, ThreadCompositionSelection.from_thread(thread))
        expected_agent = agent_id or "agent-assistant"
        expected_model = model_id or ("model-alternate" if agent_id else "model-primary")
        assert source.bot_agent_id == composition.root.base_agent_id == expected_agent
        assert source.bot_model_id == composition.root.model.model_id == expected_model
        assert root.read_bytes() == original
        document["defaults"]["agent"] = "agent-coordinator"
        root.write_text(yaml.safe_dump(document))
        next_source = await app.reload_configuration()
        assert next_source.bot_agent_id == "agent-coordinator"
        assert next_source.bot_model_id == (model_id or "model-alternate")
        assert next_source.source_digest != source.source_digest
        # Immutable resolved composition retains the original policy provenance.
        assert composition.root.base_agent_id == expected_agent
        assert composition.root.model.model_id == expected_model


@pytest.mark.parametrize("field", ["agent", "model"])
@pytest.mark.parametrize("deleted", [False, True])
async def test_bot_invalid_or_deleted_selection_retains_accepted_generation(tmp_path: Path, field: str, deleted: bool):
    root = _write_configuration(tmp_path)
    document = yaml.safe_load(root.read_text())
    document["webui"]["bot"].update(agent="agent-assistant", model="model-primary")
    root.write_text(yaml.safe_dump(document))
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        source = await app.current_configuration()
        if deleted:
            path = "agents/assistant.yaml" if field == "agent" else "models/primary.yaml"
            (tmp_path / path).unlink()
        else:
            document["webui"]["bot"][field] = f"{field}-missing"
            root.write_text(yaml.safe_dump(document))
        retained = await app.reload_configuration()
        assert retained.source_digest == source.source_digest
        assert retained.bot_agent_id == "agent-assistant"
        assert retained.bot_model_id == "model-primary"
        assert (await app.status()).candidate_error_code is not None


async def test_bot_selected_agent_without_model_is_unavailable_until_model_override(tmp_path: Path):
    root = _write_configuration(tmp_path)
    (tmp_path / "agents/coordinator.yaml").write_text(
        'schema_version: "1"\nkind: agent\nid: agent-coordinator\nname: Coordinator\n'
    )
    document = yaml.safe_load(root.read_text())
    document["webui"]["bot"]["agent"] = "agent-coordinator"
    root.write_text(yaml.safe_dump(document))
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        assert (await app.current_configuration()).bot_model_id is None
        assert (await app.bot().snapshot()).model_id is None
        document["webui"]["bot"]["model"] = "model-primary"
        root.write_text(yaml.safe_dump(document))
        assert (await app.reload_configuration()).bot_model_id == "model-primary"


async def test_bot_agent_changes_apply_to_next_run_without_relabeling_active_or_saved_capture(
    tmp_path: Path, monkeypatch
):
    root = _write_configuration(tmp_path)
    (tmp_path / "models/alternate.yaml").write_text(
        (tmp_path / "models/primary.yaml").read_text().replace("model-primary", "model-alternate")
        + "model_characteristics: {context_window_tokens: 42000}\n"
    )
    (tmp_path / "agents/coordinator.yaml").write_text(
        (tmp_path / "agents/assistant.yaml")
        .read_text()
        .replace("agent-assistant", "agent-coordinator")
        .replace("model-primary", "model-alternate")
        + "capabilities:\n  - capability: runtime_context\n    configuration: {context_window_tokens: 42000}\n"
    )
    started, release = Event(), Event()

    async def stream(messages, info):
        started.set()
        await release.wait()
        yield "Bot answer"

    async def resolve(self, model_id, *, thread_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await bot.submit(BotSubmit(input_id="selection-first", text="First"))
        with fail_after(10):
            await started.wait()
        before = await app.inspect_thread_configuration(BOT_THREAD_ID)
        assert before.captured.agent.base_agent_id == "agent-assistant"
        document = yaml.safe_load(root.read_text())
        document["webui"]["bot"]["agent"] = "agent-coordinator"
        root.write_text(yaml.safe_dump(document))
        await app.reload_configuration()
        active = await app.inspect_thread_configuration(BOT_THREAD_ID)
        assert active.next_model_id == "model-alternate"
        assert active.captured == before.captured
        assert (await bot.snapshot()).agent_id == "agent-coordinator"
        release.set()
        assert (await consumed(app, "selection-first")).state == "completed"
        saved = await app.inspect_thread_configuration(BOT_THREAD_ID)
        assert saved.capture_source == "selected_continuation"
        assert saved.captured.agent.base_agent_id == "agent-assistant"
        started, release = Event(), Event()
        await bot.submit(BotSubmit(input_id="selection-next", text="Next"))
        with fail_after(10):
            await started.wait()
        current = await app.inspect_thread_configuration(BOT_THREAD_ID)
        assert current.captured.agent.base_agent_id == "agent-coordinator"
        assert current.captured.agent.model_id == "model-alternate"
        assert current.captured.generation_digest != saved.captured.generation_digest
        assert current.captured.composition_id != saved.captured.composition_id
        assert (await app.context_usage(BOT_THREAD_ID)).context_window == 42000
        release.set()
        assert (await consumed(app, "selection-next")).state == "completed"
