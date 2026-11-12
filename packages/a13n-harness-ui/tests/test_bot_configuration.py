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
async def test_bot_inherits_default_execution_policy_but_replaces_conversational_role(tmp_path: Path, override: bool):
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
            "shell_enabled": False,
            "computer_enabled": False,
        }
        assert node.children[0].definition.instructions == ("Child policy",)
        inspection = await app.inspect_thread_configuration(BOT_THREAD_ID)
        assert inspection.next_model_id == node.model.model_id
        assert set(inspection.next_capability_ids) == set(capabilities)
        assert inspection.captured is None


async def test_bot_compacts_automatically_after_inheriting_default_agent_capability(tmp_path: Path, monkeypatch):
    root = _write_configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(
        agent.read_text() + "capabilities:\n  - capability: compaction\n    configuration:\n      trigger_tokens: 10\n"
    )
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
        assert current.latest_request_tokens is None
        assert current.record_id is None
        release.set()
        assert (await consumed(app, "meter-second")).state == "completed"
        assert (await app.context_usage(BOT_THREAD_ID)).context_window == 90000


async def test_bot_narrows_inherited_tools_for_its_files_only_chat_policy(tmp_path: Path, monkeypatch):
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
        assert not ({"ask_user_question", "shell_exec", "shell_info", "computer_click"} & set(observed))
        agent["tool_search"] = {
            "groups": {"questions": {"description": "Questions", "capabilities": ["user_interaction"]}}
        }
        agent_path.write_text(yaml.safe_dump(agent))
        source = await app.reload_configuration()
        thread = await app._threads.get(BOT_THREAD_ID)
        node = app._mcp_app_owners.resolver.resolve_agent(source, ThreadCompositionSelection.from_thread(thread))
        assert node.tool_search is not None
        assert all("user_interaction" not in group.capabilities for group in node.tool_search.groups.values())
        assert node.tools == ("list_threads",)


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
        assert "user_interaction" not in {item.capability for item in node.capabilities}
