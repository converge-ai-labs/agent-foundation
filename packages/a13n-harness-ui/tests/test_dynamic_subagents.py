from __future__ import annotations

import json

import pytest
import yaml
from a13n_harness.content import request_input_content
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BotSubmit
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.configuration.models import (
    AgentCreationConfiguration,
    DynamicSubagentsConfiguration,
    HarnessUiDocument,
    SubagentsConfiguration,
)
from a13n_harness_ui.errors import ConfigurationError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.subagent_tools import guidance
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus, RunModelOverrides
from anyio import Event, fail_after, sleep
from pydantic import ValidationError
from pydantic_ai.messages import ModelRequest, SystemPromptPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings
from .test_bot import _write_configuration as bot_configuration
from .test_bot import consumed, ready
from .test_builtin_subagents import _source
from .test_live_mode import live_configuration

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


def test_defaults_and_invalid_subagent_configuration():
    config = SubagentsConfiguration()
    assert config.max_concurrent == 8
    assert not config.dynamic.enabled
    creation = AgentCreationConfiguration()
    assert creation.allowed_agents is None and creation.default_agent is None
    assert creation.allowed_models == () and creation.default_model is None
    for bad in (
        {"max_concurrent": 0},
        {"max_concurrent": True},
        {"dynamic": {"allowed_models": ["model-a"]}},
        {"dynamic": {"default_model": "model-a"}},
    ):
        with pytest.raises(ValidationError):
            SubagentsConfiguration.model_validate(bad)
    with pytest.raises(ValidationError, match="agent_creation"):
        DynamicSubagentsConfiguration.model_validate({"allowed_models": ["model-a"]})
    with pytest.raises(ValidationError, match="agent_creation"):
        DynamicSubagentsConfiguration.model_validate({"default_model": "model-a"})
    for bad_creation in (
        {"allowed_agents": ["agent-a", "agent-a"]},
        {"allowed_agents": ["agent-a"], "default_agent": "agent-b"},
        {"allowed_agents": [], "default_agent": "agent-a"},
        {"allowed_models": ["model-a", "model-a"]},
        {"default_model": "model-a"},
        {"allowed_models": ["model-a"], "default_model": "model-b"},
    ):
        with pytest.raises(ValidationError):
            AgentCreationConfiguration.model_validate(bad_creation)
    minimal = HarnessUiDocument.model_validate({"subagents": {"dynamic": {"enabled": True}}})
    assert minimal.subagents.dynamic.enabled
    assert minimal.agent_creation == creation
    doc = HarnessUiDocument.model_validate(
        {
            "subagents": {"dynamic": {"enabled": True}},
            "agent_creation": {
                "allowed_agents": ["agent-a", "acp-b"],
                "default_agent": "acp-b",
                "allowed_models": ["model-a", "model-b"],
                "default_model": "model-b",
            },
        }
    )
    assert doc.agent_creation.default_agent == "acp-b"
    assert doc.agent_creation.default_model == "model-b"

    # Legacy subagents.dynamic.{allowed_models, default_model} lift cleanly into agent_creation without mutating input.
    legacy_input = {
        "agent_creation": {"allowed_agents": ["agent-a"]},
        "subagents": {
            "dynamic": {"enabled": True, "allowed_models": ["model-a", "model-b"], "default_model": "model-b"}
        },
    }
    legacy_snapshot = json.loads(json.dumps(legacy_input))
    lifted = HarnessUiDocument.model_validate(legacy_input)
    assert legacy_input == legacy_snapshot
    assert lifted.subagents.dynamic.enabled is True
    assert lifted.subagents.dynamic.model_extra == {}
    assert lifted.agent_creation.allowed_agents == ("agent-a",)
    assert lifted.agent_creation.allowed_models == ("model-a", "model-b")
    assert lifted.agent_creation.default_model == "model-b"

    # Agreeing legacy and canonical keys succeed; conflicting keys raise clear errors without mutating input.
    agreeing = HarnessUiDocument.model_validate(
        {
            "agent_creation": {"allowed_models": ("model-a",), "default_model": "model-a"},
            "subagents": {"dynamic": {"enabled": True, "allowed_models": ["model-a"], "default_model": "model-a"}},
        }
    )
    assert agreeing.agent_creation.allowed_models == ("model-a",)
    assert agreeing.agent_creation.default_model == "model-a"
    for conflict, pattern in (
        (
            {
                "agent_creation": {"allowed_models": ["model-a"]},
                "subagents": {"dynamic": {"allowed_models": ["model-b"]}},
            },
            "subagents.dynamic.allowed_models and agent_creation.allowed_models must agree",
        ),
        (
            {
                "agent_creation": {"allowed_models": ["model-a"], "default_model": "model-a"},
                "subagents": {"dynamic": {"default_model": "model-b"}},
            },
            "subagents.dynamic.default_model and agent_creation.default_model must agree",
        ),
        (
            {
                "agent_creation": {"allowed_models": ["model-a"], "default_model": None},
                "subagents": {"dynamic": {"default_model": "model-a"}},
            },
            "subagents.dynamic.default_model and agent_creation.default_model must agree",
        ),
    ):
        original = json.loads(json.dumps(conflict))
        with pytest.raises(ValidationError, match=pattern):
            HarnessUiDocument.model_validate(conflict)
        assert conflict == original


async def test_dynamic_model_references_are_validated(tmp_path):
    path = _source(tmp_path, ())
    path.write_text('schema_version: "1"\nagent_creation: {allowed_models: [model-missing]}\n')
    with pytest.raises(ConfigurationError) as error:
        await load_harness_ui_configuration(path)
    assert "agent_creation.allowed_models" in str(error.value)
    path.write_text('schema_version: "1"\nagent_creation: {allowed_agents: [agent-missing]}\n')
    with pytest.raises(ConfigurationError):
        await load_harness_ui_configuration(path)
    path.write_text('schema_version: "1"\nagent_creation: {default_agent: agent-missing}\n')
    with pytest.raises(ConfigurationError):
        await load_harness_ui_configuration(path)
    # Legacy subagents.dynamic.allowed_models lifts and preserves agent_creation.allowed_models error attribution.
    path.write_text('schema_version: "1"\nsubagents: {dynamic: {allowed_models: [model-missing]}}\n')
    with pytest.raises(ConfigurationError) as legacy_error:
        await load_harness_ui_configuration(path)
    assert "agent_creation.allowed_models" in str(legacy_error.value)


async def open_app(
    tmp_path,
    monkeypatch,
    model,
    *,
    enabled=True,
    limit=8,
    include=("explorer",),
    dynamic=None,
    agent_creation=None,
    steering_mode="immediate",
):
    path = _source(tmp_path, include)
    (tmp_path / "AGENTS.md").write_text("GLOBAL_GUIDANCE")
    if dynamic is not None:
        dynamic_enabled = dynamic.get("enabled", False)
        creation = {k: v for k, v in dynamic.items() if k in {"allowed_models", "default_model"}}
    else:
        dynamic_enabled = enabled
        creation = (
            agent_creation
            if agent_creation is not None
            else {"allowed_models": ["model-main"], "default_model": "model-main"}
        )
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "input": {"steering_mode": steering_mode},
                "agent_creation": creation,
                "subagents": {
                    "include": list(include),
                    "max_concurrent": limit,
                    "dynamic": {"enabled": dynamic_enabled},
                },
            }
        )
    )
    agent = tmp_path / "agents/main.yaml"
    data = yaml.safe_load(agent.read_text())
    data["instructions"] = "PARENT_PRIVATE_ROLE"
    agent.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("TEST_KEY", "test")

    async def build(self, recipe, authentication):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "_api_key_model", build)
    return open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False),
        configuration_path=path,
    )


def returned(messages):
    return [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]


async def new_thread(app):
    return await app.create_thread(
        defaults=NewThreadDefaults(
            agent_id="agent-main", project_id="project-main", environment_profile_id="environment-native"
        )
    )


async def test_dynamic_tool_is_absent_when_disabled(tmp_path, monkeypatch):
    async def model(messages, info):
        assert "spawn_subagent" not in {tool.name for tool in info.function_tools}
        assert "send_parent_message" not in {tool.name for tool in info.function_tools}
        yield "done"

    async with await open_app(tmp_path, monkeypatch, model, enabled=False) as app:
        thread = await new_thread(app)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="start")
        assert (
            await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        ).status is RootOperationStatus.completed


@pytest.mark.parametrize("allowed", [[], ["model-main"]])
async def test_delegate_roster_uses_effective_child_models(tmp_path, monkeypatch, allowed):
    control_tools = {
        "delegate",
        "subagent_info",
        "wait_subagent",
        "steer_subagent",
        "cancel_subagent",
        "resume_subagent",
    }

    async def model(messages, info):
        tools = {tool.name for tool in info.function_tools}
        if "send_parent_message" in tools:
            yield "CHILD_RESULT"
        elif not returned(messages):
            assert control_tools <= tools
            assert ("agent-reviewer:" in info.instructions) == bool(allowed)
            assert guidance("parent") in info.instructions
            assert "explorer:" in info.instructions
            yield {0: DeltaToolCall(name="delegate", json_args='{"subagent_name":"explorer","prompt":"Task"}')}
        else:
            assert returned(messages)[0].content["execution_id"]
            yield "done"

    context = await open_app(tmp_path, monkeypatch, model, dynamic={"allowed_models": allowed})
    alternative = yaml.safe_load((tmp_path / "models/main.yaml").read_text())
    alternative["id"] = "model-alt"
    (tmp_path / "models/alt.yaml").write_text(yaml.safe_dump(alternative))
    parent_path = tmp_path / "agents/main.yaml"
    parent = yaml.safe_load(parent_path.read_text())
    (tmp_path / "agents/reviewer.yaml").write_text(yaml.safe_dump({**parent, "id": "agent-reviewer"}))
    parent_path.write_text(yaml.safe_dump({**parent, "subagents": [{"agent": "agent-reviewer"}]}))
    async with context as app:
        thread = await new_thread(app)
        # The built-in explorer inherits this composer override. The reviewer keeps model-main.
        receipt = await app.submit_thread(
            thread_id=thread.thread_id, prompt="start", model_overrides=RunModelOverrides(model_id="model-alt")
        )
        assert (
            await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        ).status is RootOperationStatus.completed


async def test_operator_spawn_rejects_when_dynamic_subagents_are_disabled(tmp_path, monkeypatch):
    from a13n_harness.capabilities import SubagentOperatorContext
    from pydantic_ai.exceptions import ToolFailed

    app_ref: list = []
    seen_error: list[str] = []

    async def model(messages, info):
        app = app_ref[0]
        with pytest.raises(ToolFailed, match="disabled") as exc:
            await app._subagent_operator.spawn(
                SubagentOperatorContext(
                    parent_thread_id=thread_ref[0],
                    parent_run_id=next(iter(app._subagent_operator._parents))[1],
                    parent_agent_instance_id=next(
                        iter(app._subagent_operator._parents.values())
                    ).instance.agent_instance_id,
                    host_refs={"thread_id": thread_ref[0]},
                ),
                name="reviewer",
                prompt="Check",
            )
        seen_error.append(str(exc.value))
        yield "done"

    thread_ref: list[str] = []
    async with await open_app(tmp_path, monkeypatch, model, enabled=False) as app:
        app_ref.append(app)
        thread = await new_thread(app)
        thread_ref.append(thread.thread_id)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="start")
        assert (
            await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        ).status is RootOperationStatus.completed
    assert seen_error


@pytest.mark.parametrize(
    ("default", "model_arg", "expected"),
    [
        (None, {}, "model-main"),
        (None, {"model": None}, "model-main"),
        (None, {"model": "model-main"}, "model-main"),
        (None, {"model": "model-alt"}, None),
        ("model-alt", {}, "model-alt"),
        ("model-alt", {"model": "model-main"}, "model-main"),
    ],
    ids=["fallback", "null", "explicit-self", "disallowed", "configured-default", "self-with-default"],
)
async def test_spawn_uses_effective_model_policy(tmp_path, monkeypatch, default, model_arg, expected):
    (tmp_path / "models").mkdir()
    (tmp_path / "models/alt.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "kind": "model",
                "id": "model-alt",
                "name": "Alt",
                "route": "openai:gpt-5-mini",
                "authentication": {"kind": "api_key", "env": "TEST_KEY"},
            }
        )
    )
    receipts = []

    async def model(messages, info):
        tools = {tool.name: tool for tool in info.function_tools}
        if "send_parent_message" in tools:
            yield "CHILD_RESULT"
        elif not returned(messages):
            models = "model-alt, model-main" if default else "model-main"
            assert f"model accepts {models}; when omitted, it uses {default or 'model-main'}." in (
                tools["spawn_subagent"].description or ""
            )
            args = {"name": "reviewer", "prompt": "TASK", **model_arg}
            yield {0: DeltaToolCall(name="spawn_subagent", json_args=json.dumps(args))}
        else:
            receipts.append(returned(messages)[0].content)
            yield "done"

    policy = {"allowed_models": [default] if default else [], "default_model": default}
    async with await open_app(tmp_path, monkeypatch, model, agent_creation=policy) as app:
        thread = await new_thread(app)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="start")
        assert (
            await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        ).status is RootOperationStatus.completed
        if expected is None:
            assert "creation_model_not_allowed" in str(receipts[0])
            assert not (await app.query_child_executions(parent_thread_id=thread.thread_id)).executions
            return
        execution_id = receipts[0]["execution_id"]
        await app._subagent_operator.wait_executions(
            parent_thread_id=thread.thread_id, execution_id=execution_id, timeout_seconds=10
        )
        page = await app.query_child_executions(parent_thread_id=thread.thread_id, execution_id=execution_id)
        model_info = page.executions[0].model
        assert model_info is not None
        expected_resource = yaml.safe_load((tmp_path / f"models/{expected.removeprefix('model-')}.yaml").read_text())
        assert (model_info.name, model_info.identity) == (expected_resource["name"], expected_resource["route"])


@pytest.mark.parametrize(
    "arguments,error",
    [
        ({"model": "model-unknown"}, "creation_model_not_allowed"),
        ({"thinking": "max"}, "thinking"),
    ],
)
async def test_spawn_rejects_invalid_admission(tmp_path, monkeypatch, arguments, error):
    async def model(messages, info):
        results = returned(messages)
        if not results:
            args = {"name": "reviewer", "prompt": "TASK", **arguments}
            yield {0: DeltaToolCall(name="spawn_subagent", json_args=json.dumps(args))}
        else:
            assert error.lower() in str(results[-1].content).lower()
            yield "done"

    async with await open_app(tmp_path, monkeypatch, model) as app:
        thread = await new_thread(app)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="start")
        assert (
            await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        ).status is RootOperationStatus.completed
        assert not (await app.query_child_executions(parent_thread_id=thread.thread_id)).executions


@pytest.mark.parametrize("message", [False, True])
async def test_leaf_instructions_and_idle_delivery(tmp_path, monkeypatch, message):
    finish, child_done, wake_seen = Event(), Event(), Event()
    receipts, parent_inputs = [], []

    async def model(messages, info):
        tools = {tool.name for tool in info.function_tools}
        if "send_parent_message" in tools:
            assert "spawn_subagent" not in tools and "delegate" not in tools
            assert "PARENT_PRIVATE_ROLE" not in (info.instructions or "")
            assert "CHILD_ROLE" not in (info.instructions or "")
            assert "CHILD_TASK" not in (info.instructions or "")
            assert guidance("child") in (info.instructions or "")
            assert "ROOT_HISTORY_SECRET" not in str(messages)
            assert "CHILD_TASK" in str(messages)
            assert "GLOBAL_GUIDANCE" in str(messages)
            assert any(
                (item.metadata.model_extra or {}).get("a13n.steering-source") == "parent_agent"
                for request in messages
                if isinstance(request, ModelRequest)
                for item in request_input_content(request)
            )
            await finish.wait()
            if message and not returned(messages):
                yield {0: DeltaToolCall(name="send_parent_message", json_args='{"message":"PROGRESS_MESSAGE"}')}
            else:
                child_done.set()
                yield "CHILD_RESULT"
            return
        parent_inputs.append(str(messages))
        if "PROGRESS_MESSAGE" in str(messages) or "Subagent result ·" in str(messages):
            wake_seen.set()
            yield "Wake handled."
        elif not returned(messages):
            yield {
                0: DeltaToolCall(
                    name="spawn_subagent",
                    json_args=json.dumps(
                        {
                            "name": "reviewer",
                            "prompt": "CHILD_TASK",
                            "model": "model-main",
                            "thinking": "high",
                        }
                    ),
                )
            }
        else:
            receipts.append(returned(messages)[-1].content)
            yield "Work continues."

    async with await open_app(tmp_path, monkeypatch, model) as app:
        thread = await new_thread(app)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="ROOT_HISTORY_SECRET")
        assert (
            await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        ).status is RootOperationStatus.completed
        assert receipts[0]["source"] == "host"
        assert "arrives here automatically" in receipts[0]["message"]
        execution_id = receipts[0]["execution_id"]
        page = await app.query_child_executions(parent_thread_id=thread.thread_id, execution_id=execution_id)
        model_info = page.executions[0].model
        assert model_info is not None
        assert (model_info.name, model_info.identity, model_info.thinking) == ("Main", "openai:gpt-5", "High")
        finish.set()
        with fail_after(10):
            await child_done.wait()
            while execution_id in await app._subagent_operator.active_execution_ids():
                await sleep(0.01)
            await wake_seen.wait()
            assert any(execution_id in text for text in parent_inputs[-1:])
        view = await app._subagent_operator.inspect_executions(
            parent_thread_id=thread.thread_id, execution_id=execution_id
        )
        assert view.executions[0].source == "host"
        assert view.executions[0].resumable


async def test_dynamic_resume_uses_host_source_and_retained_role(tmp_path, monkeypatch):
    child_calls, execution_ids = [], []

    async def model(messages, info):
        if "send_parent_message" in {tool.name for tool in info.function_tools}:
            assert "initial task" in str(messages)
            child_calls.append(messages)
            yield "child done"
            return
        results = returned(messages)
        if not results:
            yield {
                0: DeltaToolCall(
                    name="spawn_subagent",
                    json_args='{"name":"reviewer","prompt":"initial task"}',
                )
            }
        elif len(results) == 1:
            execution_ids.append(results[0].content["execution_id"])
            yield {0: DeltaToolCall(name="wait_subagent", json_args=json.dumps({"execution_id": execution_ids[0]}))}
        elif len(results) == 2:
            yield {
                0: DeltaToolCall(
                    name="resume_subagent",
                    json_args=json.dumps({"execution_id": execution_ids[0], "prompt": "new task"}),
                )
            }
        else:
            receipt = results[2].content
            assert receipt["source"] == "host" and receipt["resumed_from"] == execution_ids[0]
            assert receipt["execution_id"] != execution_ids[0]
            execution_ids.append(receipt["execution_id"])
            yield "done"

    async with await open_app(tmp_path, monkeypatch, model) as app:
        thread = await new_thread(app)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="start")
        assert (
            await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        ).status is RootOperationStatus.completed
        await app._subagent_operator.wait_executions(
            parent_thread_id=thread.thread_id, execution_id=execution_ids[1], timeout_seconds=10
        )
        assert len(child_calls) == 2
        assert "new task" in str(child_calls[1])
        for messages, prompt in zip(child_calls, ("initial task", "new task"), strict=True):
            inputs = [
                item
                for request in messages
                if isinstance(request, ModelRequest)
                for item in request_input_content(request)
            ]
            marked = [item for item in inputs if prompt in str(item.value)]
            assert marked and all(
                (item.metadata.model_extra or {}).get("a13n.steering-source") == "parent_agent" for item in marked
            )


@pytest.mark.parametrize(("spawn", "wait_for_successor"), [("delegate", False), ("spawn_subagent", True)])
async def test_repeated_resume_returns_tool_error_without_failing_parent(
    tmp_path, monkeypatch, spawn, wait_for_successor
):
    release = Event()
    execution_ids, child_calls = [], []

    async def model(messages, info):
        if "send_parent_message" in {tool.name for tool in info.function_tools}:
            child_calls.append(messages)
            if len(child_calls) == 2 and not wait_for_successor:
                await release.wait()
            yield "child done"
            return
        results = returned(messages)
        if not results:
            arguments = (
                {"subagent_name": "explorer", "prompt": "initial task"}
                if spawn == "delegate"
                else {"name": "worker", "prompt": "initial task"}
            )
            yield {0: DeltaToolCall(name=spawn, json_args=json.dumps(arguments))}
        elif len(results) == 1:
            execution_ids.append(results[0].content["execution_id"])
            yield {0: DeltaToolCall(name="wait_subagent", json_args=json.dumps({"execution_id": execution_ids[0]}))}
        elif len(results) == 2:
            yield {
                0: DeltaToolCall(
                    name="resume_subagent",
                    json_args=json.dumps({"execution_id": execution_ids[0], "prompt": "accepted follow-up"}),
                )
            }
        elif len(results) == 3:
            execution_ids.append(results[2].content["execution_id"])
            if wait_for_successor:
                yield {0: DeltaToolCall(name="wait_subagent", json_args=json.dumps({"execution_id": execution_ids[1]}))}
            else:
                yield {
                    0: DeltaToolCall(
                        name="resume_subagent",
                        json_args=json.dumps({"execution_id": execution_ids[0], "prompt": "rejected follow-up"}),
                    )
                }
        elif len(results) == 4 and wait_for_successor:
            yield {
                0: DeltaToolCall(
                    name="resume_subagent",
                    json_args=json.dumps({"execution_id": execution_ids[0], "prompt": "rejected follow-up"}),
                )
            }
        else:
            error = str(results[-1].content)
            assert "child_execution_already_resumed" in error
            assert execution_ids[1] in error
            yield "Parent recovered."

    async with await open_app(tmp_path, monkeypatch, model) as app:
        app._tool_executions.deliver_subagent = None
        try:
            thread = await new_thread(app)
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="start")
            result = await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
            assert result.status is RootOperationStatus.completed, result
            release.set()
            await app._subagent_operator.wait_executions(
                parent_thread_id=thread.thread_id, execution_id=execution_ids[1], timeout_seconds=10
            )
            page = await app.query_child_executions(parent_thread_id=thread.thread_id)
            assert page.total == 2
            assert len(child_calls) == 2
            assert "rejected follow-up" not in str(child_calls)
        finally:
            release.set()


@pytest.mark.parametrize(
    "surface,role",
    [
        ("bot", "You are Bot, the instance's persistent personal assistant."),
        ("live", "You are the user's Live task coordinator."),
    ],
)
async def test_dynamic_child_of_bot_or_live_gets_the_package_prompt(tmp_path, monkeypatch, surface, role):
    root = bot_configuration(tmp_path) if surface == "bot" else live_configuration(tmp_path)
    value = yaml.safe_load(root.read_text())
    value["agent_creation"] = {"allowed_models": ["model-primary"], "default_model": "model-primary"}
    value["subagents"] = {"dynamic": {"enabled": True}}
    root.write_text(yaml.safe_dump(value))
    parent_instructions, child_instructions, child_seen = [], [], Event()

    def prompt(messages, info):
        parts = [part.content for request in messages for part in request.parts if isinstance(part, SystemPromptPart)]
        return "\n".join([*parts, info.instructions or ""])

    async def stream(messages, info):
        if "send_parent_message" in {tool.name for tool in info.function_tools}:
            child_instructions.append(prompt(messages, info))
            child_seen.set()
            yield "child done"
        elif returned(messages):
            yield "done"
        else:
            parent_instructions.append(prompt(messages, info))
            arguments = {"name": "reviewer", "prompt": "CHILD_TASK"}
            yield {0: DeltaToolCall(name="spawn_subagent", json_args=json.dumps(arguments))}

    async def resolve(self, model_id, *, thread_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        if surface == "bot":
            await (await ready(app)).submit(BotSubmit(input_id="input-spawn", text="start"))
            assert (await consumed(app, "input-spawn")).state == "completed"
        else:
            thread = await app._threads.live_thread(configuration_root=tmp_path)
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="start")
            assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        with fail_after(10):
            await child_seen.wait()
    assert role in parent_instructions[0]
    assert role not in child_instructions[0]
    assert "You are the Harness UI CLI Agent" in child_instructions[0]
    assert "You are a subagent" in child_instructions[0]


@pytest.mark.parametrize("first,second", [("delegate", "spawn_subagent")])
async def test_concurrency_limit_is_shared_by_declared_and_dynamic(tmp_path, monkeypatch, first, second):
    release = Event()

    def args(tool):
        return (
            {"subagent_name": "explorer", "prompt": "task"}
            if tool == "delegate"
            else {"name": "worker", "prompt": "task"}
        )

    async def model(messages, info):
        if "send_parent_message" in {tool.name for tool in info.function_tools}:
            await release.wait()
            yield "done"
            return
        results = returned(messages)
        if len(results) < 2:
            tool = first if not results else second
            yield {0: DeltaToolCall(name=tool, json_args=json.dumps(args(tool)))}
        else:
            assert "subagents.max_concurrent" in str(results[-1].content)
            yield "limit respected"

    async with await open_app(tmp_path, monkeypatch, model, limit=1) as app:
        try:
            thread = await new_thread(app)
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="start")
            assert (
                await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
            ).status is RootOperationStatus.completed
            assert len(await app._subagent_operator.active_execution_ids()) == 1
        finally:
            release.set()


async def test_resume_uses_the_same_concurrency_limit(tmp_path, monkeypatch):
    release = Event()
    first_id = None

    async def model(messages, info):
        nonlocal first_id
        if "send_parent_message" in {tool.name for tool in info.function_tools}:
            if "BLOCKED_ROLE" in str(messages):
                await release.wait()
            yield "done"
            return
        results = returned(messages)
        if not results:
            yield {0: DeltaToolCall(name="spawn_subagent", json_args='{"name":"first","prompt":"task"}')}
        elif len(results) == 1:
            first_id = results[0].content["execution_id"]
            yield {0: DeltaToolCall(name="wait_subagent", json_args=json.dumps({"execution_id": first_id}))}
        elif len(results) == 2:
            yield {0: DeltaToolCall(name="spawn_subagent", json_args='{"name":"second","prompt":"BLOCKED_ROLE task"}')}
        elif len(results) == 3:
            yield {
                0: DeltaToolCall(
                    name="resume_subagent", json_args=json.dumps({"execution_id": first_id, "prompt": "continue"})
                )
            }
        else:
            assert "subagents.max_concurrent" in str(results[-1].content)
            yield "limit respected"

    async with await open_app(tmp_path, monkeypatch, model, limit=1) as app:
        try:
            thread = await new_thread(app)
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="start")
            assert (
                await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
            ).status is RootOperationStatus.completed
            assert len(await app._subagent_operator.active_execution_ids()) == 1
        finally:
            release.set()


@pytest.mark.parametrize(("single", "mode"), [(True, "immediate"), (False, "queued")])
async def test_user_message_during_subagent_wait(tmp_path, monkeypatch, single, mode):
    finish_child, waiting, user_seen, result_seen = Event(), Event(), Event(), Event()
    wait_results = []

    async def model(messages, info):
        if "send_parent_message" in {tool.name for tool in info.function_tools}:
            await finish_child.wait()
            yield "CHILD_FINISHED"
            return
        results = returned(messages)
        if not results:
            yield {0: DeltaToolCall(name="delegate", json_args='{"subagent_name":"explorer","prompt":"inspect"}')}
        elif results[-1].tool_name == "delegate":
            args = {"timeout_seconds": 180}
            if single:
                args["execution_id"] = results[-1].content["execution_id"]
            yield {0: DeltaToolCall(name="wait_subagent", json_args=json.dumps(args))}
        else:
            wait_results.append(results[-1].content)
            if "NEW_USER_INPUT" in str(messages):
                user_seen.set()
            if "CHILD_FINISHED" in str(messages):
                result_seen.set()
            yield "Received."

    async with await open_app(tmp_path, monkeypatch, model, steering_mode=mode) as app:
        restart = app._subagent_operator._restart
        wait_child = restart.wait_child

        async def observe_wait(event, timeout):
            waiting.set()
            await wait_child(event, timeout)

        monkeypatch.setattr(restart, "wait_child", observe_wait)
        thread = await new_thread(app)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="start")
        try:
            with fail_after(10):
                await waiting.wait()
                response = await app.steer_root_operation(receipt_id=receipt.receipt_id, message="NEW_USER_INPUT")
                assert response.accepted
                if mode == "immediate":
                    await user_seen.wait()
                    assert wait_results[0]["executions"][0]["status"] == "running"
                    assert len(await app._subagent_operator.active_execution_ids()) == 1
                else:
                    assert not user_seen.is_set()
                finish_child.set()
                await user_seen.wait()
                await result_seen.wait()
                if mode == "queued":
                    assert wait_results[0]["executions"][0]["status"] == "succeeded"
        finally:
            finish_child.set()


async def test_plain_agent_has_no_subagent_surface_or_parent_prompt(tmp_path, monkeypatch):
    async def model(messages, info):
        names = {tool.name for tool in info.function_tools}
        assert not names & {
            "delegate",
            "subagent_info",
            "wait_subagent",
            "steer_subagent",
            "cancel_subagent",
            "resume_subagent",
            "spawn_subagent",
            "send_parent_message",
        }
        assert guidance("parent") not in (info.instructions or "")
        assert "Use `delegate` to start one declared subagent" not in (info.instructions or "")
        assert "Final results arrive automatically" not in (info.instructions or "")
        inputs = [
            item for request in messages if isinstance(request, ModelRequest) for item in request_input_content(request)
        ]
        assert all(item.metadata.source_id != "a13n-harness-ui.subagents.summary" for item in inputs)
        assert "Check outstanding subagent work" not in str(messages)
        yield "done"

    async with await open_app(tmp_path, monkeypatch, model, enabled=False, include=()) as app:
        thread = await new_thread(app)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="start")
        assert (
            await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        ).status is RootOperationStatus.completed


async def test_idle_declared_child_keeps_grandchild_results_without_automatic_resume(tmp_path, monkeypatch):
    release, result_routed = Event(), Event()
    child_id, grandchild_id = None, None

    async def model(messages, info):
        nonlocal child_id, grandchild_id
        names = {tool.name for tool in info.function_tools}
        results = returned(messages)
        if "CHILD_PARENT_ROLE" in (info.instructions or ""):
            assert {"delegate", "subagent_info", "wait_subagent", "send_parent_message"} <= names
            assert "spawn_subagent" not in names
            assert (info.instructions or "").count(guidance("child")) == 1
            assert (info.instructions or "").count(guidance("parent")) == 1
            assert (info.instructions or "").count("CHILD_PARENT_ROLE") == 1
            if not results:
                yield {
                    0: DeltaToolCall(
                        name="delegate", json_args='{"subagent_name":"explorer","prompt":"Grandchild task"}'
                    )
                }
            else:
                grandchild_id = results[-1].content["execution_id"]
                assert "a13n-harness-ui.subagents.summary" in str(messages)
                yield "Child finished while grandchild runs."
        elif "send_parent_message" in names:
            await release.wait()
            if not results:
                yield {0: DeltaToolCall(name="send_parent_message", json_args='{"message":"GRANDCHILD_PROGRESS"}')}
            else:
                yield "GRANDCHILD_RESULT"
        elif not results and "Subagent result ·" not in str(messages):
            yield {
                0: DeltaToolCall(name="delegate", json_args='{"subagent_name":"agent-reviewer","prompt":"Child task"}')
            }
        else:
            if results and results[0].tool_name == "delegate":
                child_id = results[0].content["execution_id"]
            yield "Root finished."

    context = await open_app(tmp_path, monkeypatch, model, include=(), agent_creation={"allowed_models": []})
    root_file = tmp_path / "agents/main.yaml"
    resource = yaml.safe_load(root_file.read_text())
    resource["subagents"] = [{"agent": "agent-reviewer"}]
    root_file.write_text(yaml.safe_dump(resource))
    (tmp_path / "agents/reviewer.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "kind": "agent",
                "id": "agent-reviewer",
                "name": "Reviewer",
                "model": "model-main",
                "instructions": "CHILD_PARENT_ROLE",
                "subagents": [{"markdown": "subagent-builtin-explorer"}],
            }
        )
    )
    async with context as app:
        thread = await new_thread(app)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Root task")
        assert (
            await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        ).status is RootOperationStatus.completed
        await app._subagent_operator.wait_executions(
            parent_thread_id=thread.thread_id, execution_id=child_id, timeout_seconds=10
        )
        child_head = await app._store.child_executions.get(child_id)
        assert child_head.status == "succeeded" and grandchild_id is not None
        original = app._subagent_operator._deliver_parent
        delivered = []

        async def observe(target, prompt, execution_ids):
            handled = await original(target, prompt, execution_ids)
            if grandchild_id in execution_ids:
                delivered.append((target, str(prompt)))
                if "GRANDCHILD_RESULT" in str(prompt):
                    result_routed.set()
            return handled

        monkeypatch.setattr(app._subagent_operator, "_deliver_parent", observe)
        release.set()
        with fail_after(10):
            await result_routed.wait()
        latest = await app._store.child_executions.latest_for_child(child_head.child_thread_id)
        assert latest.execution_id == child_id and latest.status == "succeeded"
        assert all(target == child_head.child_thread_id for target, _ in delivered)
        assert any("GRANDCHILD_PROGRESS" in text for _, text in delivered)
        retained = await app._subagent_operator.inspect_executions(
            parent_thread_id=child_head.child_thread_id,
            execution_id=grandchild_id,
        )
        assert retained.executions[0].status == "succeeded"
        assert "GRANDCHILD_RESULT" in retained.model_dump_json()
