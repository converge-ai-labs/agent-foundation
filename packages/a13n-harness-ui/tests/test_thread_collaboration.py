from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition import ResolvedRunComposition
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.configuration.discovery import resource_page
from a13n_harness_ui.errors import ConfigurationError, ThreadError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import RootControlResult, RootOperationStatus
from a13n_harness_ui.thread_capability import ThreadCollaborationCapability, ThreadToolController
from anyio import Event, fail_after
from pydantic_ai.messages import TextContent, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _CompletedReconstructor, _settings, _write_configuration

pytestmark = pytest.mark.anyio


def configuration(tmp_path: Path) -> Path:
    root = _write_configuration(tmp_path)
    (tmp_path / "agents/worker.yaml").write_text(
        "schema_version: '1'\nkind: agent\nid: agent-worker\nname: Worker\nmodel: model-secondary\n"
        "instructions: private-worker-instructions\nmcp_servers: []\n"
    )
    (tmp_path / "models/secondary.yaml").write_text(
        "schema_version: '1'\nkind: model\nid: model-secondary\nname: Secondary\nroute: openai:gpt-5\n"
        "authentication: {kind: api_key, env: PRIVATE_MODEL_KEY}\nmodel_configuration: {base_url: 'https://private.test'}\n"
    )
    workspace = tmp_path / "other"
    workspace.mkdir()
    (tmp_path / "projects/other.yaml").write_text(
        f"schema_version: '1'\nkind: project\nid: project-other\nname: Other\nroots:\n  - path: {workspace}\n"
        "defaults: {agent: agent-worker, mcp_servers: []}\n"
    )
    return root


def controller(app) -> ThreadToolController:
    return ThreadToolController(
        projections=app._projections,
        root_runs=app._root_runs,
        create_thread=app.create_thread,
        configurations=app._configurations,
        inspect_configuration=app.inspect_thread_configuration,
    )


async def test_resource_discovery_is_filtered_paginated_and_credential_free(tmp_path: Path) -> None:
    source = await load_harness_ui_configuration(configuration(tmp_path))
    first = resource_page(source, kind="agents", query=None, cursor=None, limit=1)
    assert first["total"] == 2 and first["agents"][0]["id"] == "agent-assistant"
    second = resource_page(source, kind="agents", query=None, cursor=first["next_cursor"], limit=1)
    assert second["agents"][0]["model_id"] == "model-secondary" and second["next_cursor"] is None
    found = resource_page(source, kind="models", query="SECONDARY", cursor=None, limit=20)
    assert found["total"] == 1
    assert found["models"] == [{"id": "model-secondary", "name": "Secondary", "route": "openai:gpt-5"}]
    assert "private" not in json.dumps([first, second, found]).lower()
    for kind, query in (("models", None), ("agents", "worker")):
        with pytest.raises(ConfigurationError, match="cursor"):
            resource_page(source, kind=kind, query=query, cursor=first["next_cursor"], limit=1)
    changed = source.model_copy(update={"source_digest": "f" * 64})
    with pytest.raises(ConfigurationError, match="cursor"):
        resource_page(changed, kind="agents", query=None, cursor=first["next_cursor"], limit=1)


@pytest.mark.parametrize(
    "project_id, expected", [("current", "project-main"), ("project-other", "project-other"), (None, None)]
)
async def test_create_selects_project_defaults_without_copying_source_tools(
    tmp_path: Path, project_id, expected
) -> None:
    root = configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        source = await app.create_thread()
        tools = controller(app)
        result = await tools.create_thread(
            source_thread_id=source.thread_id,
            prompt="Work",
            title=None,
            agent_id="agent-worker",
            project_id=project_id,
        )
        assert result["ok"]
        created = (await app.get_thread(result["thread_id"])).thread
        assert created.configuration.project_id == expected
        assert created.configuration.agent_source.id == "agent-worker"
        assert created.parent_thread_id is None
        assert (await app.wait_root_operation(result["receipt"]["receipt_id"])).status is RootOperationStatus.completed
        project = await tools.get_project("project-other")
        assert project["project"]["roots"] == [str(tmp_path / "other")]
        listed = await tools.list_threads(query=None, cursor=None, limit=20, project_id="project-main")
        assert all(item["configuration"]["project_id"] == "project-main" for item in listed["threads"])
        with pytest.raises(ThreadError):
            await tools.create_thread(
                source_thread_id=source.thread_id,
                prompt="Work",
                title=None,
                agent_id=None,
                project_id="project-missing",
            )
        assert (await app.list_threads()).total == 2


@pytest.mark.parametrize("model_id", ["", "x" * 129])
async def test_invalid_model_override_has_no_creation_or_admission_effect(tmp_path: Path, model_id: str) -> None:
    root = configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        source = await app.create_thread()
        target = await app.create_thread()
        capability = ThreadCollaborationCapability(controller=controller(app), source_thread_id=source.thread_id)
        ctx = SimpleNamespace(deps=SimpleNamespace(thread_id=source.thread_id))
        created = await capability.create_thread(ctx, prompt="Work", model_id=model_id)
        assert created["ok"] is False and created["error"]["code"] == "thread_create_failed"
        assert (await app.list_threads()).total == 2
        started = await capability.run_thread(ctx, thread_id=target.thread_id, prompt="Work", model_id=model_id)
        assert started["ok"] is False and started["error"]["code"] == "thread_run_failed"
        assert await app._root_runs.active(target.thread_id) is None


async def test_rejected_message_never_starts_a_replacement_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        target = await app.create_thread()
        source = await app.create_thread(title="Requester")
        calls = []

        async def active(thread_id):
            return SimpleNamespace(receipt=SimpleNamespace(receipt_id="receipt-captured"))

        async def steer(*, receipt_id, message):
            calls.append((receipt_id, message))
            return RootControlResult(receipt_id=receipt_id, accepted=False)

        async def submit(**kwargs):
            pytest.fail("A rejected steer must not start another Run")

        monkeypatch.setattr(app._root_runs, "active", active)
        monkeypatch.setattr(app._root_runs, "steer", steer)
        monkeypatch.setattr(app._root_runs, "submit_prompt", submit)
        result = await controller(app).send_thread_message(
            source_thread_id=source.thread_id, thread_id=target.thread_id, message="Report"
        )
        assert result == {
            "ok": False,
            "mode": "steer",
            "receipt_id": "receipt-captured",
            "accepted": False,
            "enqueue_id": None,
        }
        assert len(calls) == 1
        receipt_id, parts = calls[0]
        assert receipt_id == "receipt-captured"
        assert parts == (
            TextContent(f"Message from Thread {source.thread_id}:", metadata={"display": False}),
            TextContent(
                "Report",
                metadata={
                    "harness_ui": {
                        "thread_message": {"source_thread_id": source.thread_id, "source_thread_title": "Requester"}
                    }
                },
            ),
        )


async def test_message_tools_reject_self_and_empty_input_before_admission() -> None:
    capability = ThreadCollaborationCapability(controller=None, source_thread_id="thread-source")
    ctx = SimpleNamespace(deps=SimpleNamespace(thread_id="thread-source"))
    assert (await capability.send_thread_message(ctx, "thread-source", "Report"))["error"][
        "code"
    ] == "thread_recursive_message"
    assert (await capability.send_thread_message(ctx, "thread-other", " "))["error"]["code"] == "thread_message_empty"


async def test_real_model_tools_discover_delegate_cross_project_and_report_to_idle_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = configuration(tmp_path)  # Sidekick is disabled: all collaboration tools still work.
    origin_id = ""
    origin_idle, reported = Event(), Event()
    steps: dict[str, int] = {}
    results: dict[str, dict] = {}
    instructions: list[str] = []
    resolved: dict[str, str] = {}

    async def resolve(self, context, model_id):
        thread_id = context.deps.thread_id
        resolved[thread_id] = self._recipes[model_id].model_id

        async def model(messages, info):
            instructions.append(info.instructions or "")
            assert f"Your current Thread: {thread_id}" in (info.instructions or "")
            expected_project = "project-main" if thread_id == origin_id else "project-other"
            assert f"Your captured Project: {expected_project}" in (info.instructions or "")
            step = steps.get(thread_id, 0)
            steps[thread_id] = step + 1
            for message in messages:
                for part in message.parts:
                    if isinstance(part, ToolReturnPart):
                        results.setdefault(thread_id, {})[part.tool_name] = part.content
            if thread_id == origin_id:
                plan = [
                    ("get_thread", {}),
                    ("list_projects", {}),
                    ("list_agents", {}),
                    ("list_models", {}),
                    ("get_project", {"project_id": "project-other"}),
                    (
                        "create_thread",
                        {"project_id": "project-other", "agent_id": "agent-worker", "prompt": "Investigate"},
                    ),
                ]
                if step < len(plan):
                    name, arguments = plan[step]
                    yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id=f"call-{step}")}
                elif step == len(plan):
                    yield "Independent work started"
                else:
                    assert "worker findings" in str(messages)
                    reported.set()
                    yield "Integrated worker findings"
            elif step == 0:
                assert origin_id in str(messages)
                assert "Requesting Project: project-main" in str(messages)
                assert f"send_thread_message(thread_id={origin_id!r}" in str(messages)
                assert "For clarification, decisions or blockers" in str(messages)
                assert "When finished" in str(messages)
                await origin_idle.wait()
                yield {
                    0: DeltaToolCall(
                        name="send_thread_message",
                        json_args=json.dumps(
                            {
                                "thread_id": origin_id,
                                "message": "worker findings",
                            }
                        ),
                        tool_call_id="call-report",
                    )
                }
            else:
                yield "Report accepted"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui", instrumentation=None) as app:
        origin = await app.create_thread(title="Release review")
        origin_id = origin.thread_id
        receipt = await app.submit_thread(thread_id=origin_id, prompt="Delegate independent work")
        with fail_after(15):
            initial = await app.wait_root_operation(receipt.receipt_id)
            assert initial.status is RootOperationStatus.completed, initial.model_dump_json()
            origin_idle.set()
            child = results[origin_id]["create_thread"]
            assert child["ok"]
            assert (
                await app.wait_root_operation(child["receipt"]["receipt_id"])
            ).status is RootOperationStatus.completed
            assert child["thread_id"] in results, {"steps": steps, "results": results, "child": child}
            report = results[child["thread_id"]]["send_thread_message"]
            assert report["ok"] and report["mode"] == "run", report
            delivered = await app.wait_root_operation(report["receipt"]["receipt_id"])
            assert delivered.status is RootOperationStatus.completed, delivered.model_dump_json()
            assert reported.is_set()
        own = results[origin_id]["get_thread"]
        assert own["current_run"]["project_id"] == "project-main"
        assert own["current_run"]["agent_id"] == "agent-assistant"
        assert results[origin_id]["list_agents"]["total"] == 2
        assert results[origin_id]["list_models"]["total"] == 2
        assert resolved[child["thread_id"]] == "model-secondary"
        worker = (await app.get_thread(child["thread_id"])).thread
        assert worker.parent_thread_id is None and worker.configuration.project_id == "project-other"
        transcript = await app.get_thread_transcript(thread_id=origin_id)
        assert "Message from Thread" in transcript.model_dump_json()
        assert "Integrated worker findings" in transcript.model_dump_json()
        for target, sender, text, template in (
            (worker.thread_id, origin_id, "Investigate", "Task from Thread"),
            (origin_id, worker.thread_id, "worker findings", "Message from Thread"),
        ):
            history = await app.get_thread_transcript(thread_id=target)
            parts = [part for entry in history.entries for part in entry.parts]
            context = next(part for part in parts if (part.text or "").startswith(template))
            assert context.metadata.display is False
            body = next(part for part in parts if part.text == text)
            attribution = body.metadata.model_dump()["harness_ui"]["thread_message"]
            assert attribution["source_thread_id"] == sender
            assert attribution["source_thread_title"] == ("Release review" if sender == origin_id else "Investigate")
            assert body.metadata.display is True
        assert (await app.get_thread(worker.thread_id)).thread.excerpt.first_input == "Investigate"
    # Provenance and visibility survive re-opening the stored conversation.
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui", instrumentation=None) as app:
        history = await app.get_thread_transcript(thread_id=worker.thread_id)
        parts = [part for entry in history.entries for part in entry.parts]
        body = next(part for part in parts if part.text == "Investigate")
        assert body.metadata.model_dump()["harness_ui"]["thread_message"]["source_thread_id"] == origin_id
        assert (
            next(part for part in parts if (part.text or "").startswith("Task from Thread")).metadata.display is False
        )
    assert all("Sidekick is enabled" not in text for text in instructions)


@pytest.mark.parametrize(
    "sidekick", [{"agent": "agent-missing"}, {"agent": "model-primary"}, {"model": "model-missing"}]
)
async def test_invalid_sidekick_configuration_is_rejected(tmp_path: Path, sidekick) -> None:
    root = configuration(tmp_path)
    document = yaml.safe_load(root.read_text())
    document["webui"] = {"sidekick": sidekick}
    root.write_text(yaml.safe_dump(document))
    with pytest.raises(ConfigurationError):
        await load_harness_ui_configuration(root)


@pytest.mark.parametrize(
    "sidekick, expected_agent, expected_model",
    [
        ({}, "agent-assistant", "model-primary"),
        ({"agent": "agent-worker"}, "agent-worker", "model-secondary"),
        ({"model": "model-secondary"}, "agent-assistant", "model-secondary"),
        ({"agent": "agent-worker", "model": "model-primary"}, "agent-worker", "model-primary"),
    ],
)
async def test_host_applies_sidekick_defaults_to_creation_and_later_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sidekick, expected_agent, expected_model
) -> None:
    root = configuration(tmp_path)
    document = yaml.safe_load(root.read_text())
    document["webui"] = {"sidekick": sidekick}
    root.write_text(yaml.safe_dump(document))
    instructions = []

    async def resolve(self, context, model_id):
        async def model(messages, info):
            instructions.append(info.instructions or "")
            yield "Completed"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        source = await app.create_thread()
        first = await app.submit_thread(thread_id=source.thread_id, prompt="Inspect preference")
        assert (await app.wait_root_operation(first.receipt_id)).status is RootOperationStatus.completed
        original = await app.inspect_operation_configuration(first.receipt_id)
        assert original.agent.model_id == "model-primary"
        assert original.webui_sidekick.agent == sidekick.get("agent")
        assert original.webui_sidekick.model == sidekick.get("model")
        assert f"Your current Thread: {source.thread_id}" in instructions[0]
        assert "Your captured Project: project-main" in instructions[0]
        assert "Answer its questions through that same tool" in instructions[0]
        assert "Use subagents for parallel research, exploration, and other bounded tasks" in instructions[0]
        assert "keep that work in the current Thread rather than creating a Sidekick as a fallback" in instructions[0]
        assert (
            "Create a separate Thread only for coordination work that needs human attention, decisions, or follow-up "
            "in its own conversation" in instructions[0]
        )
        assert "For useful independent work, prefer create_thread" not in instructions[0]
        assert f"Agent {expected_agent!r}" in instructions[0]
        assert "Host applies the captured Sidekick defaults" in instructions[0]
        reference = await app._root_runs.composition_reference(first.receipt_id)
        composition = await app._store.objects.read_model(reference, ResolvedRunComposition)
        if "model" in sidekick:
            assert f"model_id={sidekick['model']!r}" in instructions[0]
        result = await controller(app).create_thread(
            source_thread_id=source.thread_id,
            prompt="Independent work",
            title=None,
            agent_id=None,
            source_composition=composition,
        )
        receipt = result["receipt"]["receipt_id"]
        assert (await app.wait_root_operation(receipt)).status is RootOperationStatus.completed
        captured = await app.inspect_operation_configuration(receipt)
        assert captured.agent.source_id == expected_agent
        assert captured.agent.model_id == expected_model
        saved = (await app.get_thread(result["thread_id"])).thread.configuration
        assert saved.default_model_id == sidekick.get("model")
        # Host defaults are persistent; explicit overrides are still per-operation.
        again = await controller(app).run_thread(
            source_thread_id=source.thread_id, thread_id=result["thread_id"], prompt="Follow-up"
        )
        assert (await app.wait_root_operation(again["receipt_id"])).status is RootOperationStatus.completed
        following = await app.inspect_operation_configuration(again["receipt_id"])
        assert following.agent.model_id == expected_model
        message = await controller(app).send_thread_message(
            source_thread_id=source.thread_id, thread_id=result["thread_id"], message="Continue via message"
        )
        receipt = message["receipt"]["receipt_id"]
        assert (await app.wait_root_operation(receipt)).status is RootOperationStatus.completed
        assert (await app.inspect_operation_configuration(receipt)).agent.model_id == expected_model
        inspected = await controller(app).get_thread(
            thread_id=result["thread_id"], history_cursor=None, history_limit=1
        )
        assert inspected["configuration"]["next_model_id"] == expected_model
        assert inspected["configuration"]["captured"]["agent"]["model_id"] == expected_model
        override = "model-secondary" if expected_model == "model-primary" else "model-primary"
        explicit = await controller(app).run_thread(
            source_thread_id=source.thread_id,
            thread_id=result["thread_id"],
            prompt="One-off override",
            model_id=override,
        )
        assert (await app.wait_root_operation(explicit["receipt_id"])).status is RootOperationStatus.completed
        assert (await app.inspect_operation_configuration(explicit["receipt_id"])).agent.model_id == override
        assert (await app.inspect_thread_configuration(result["thread_id"])).next_model_id == expected_model
        # Explicit creation Agent and Model win independently; the configured default remains durable.
        explicit_create = await controller(app).create_thread(
            source_thread_id=source.thread_id,
            prompt="Explicit choices",
            title=None,
            agent_id="agent-assistant",
            model_id=override,
            source_composition=composition,
        )
        explicit_receipt = explicit_create["receipt"]["receipt_id"]
        assert (await app.wait_root_operation(explicit_receipt)).status is RootOperationStatus.completed
        explicit_capture = await app.inspect_operation_configuration(explicit_receipt)
        assert explicit_capture.agent.source_id == "agent-assistant"
        assert explicit_capture.agent.model_id == override
        assert (
            await app.get_thread(explicit_create["thread_id"])
        ).thread.configuration.default_model_id == sidekick.get("model")
    # Disabling Sidekick and reopening the App must not rewrite existing Threads.
    document["webui"] = {"sidekick": None}
    root.write_text(yaml.safe_dump(document))
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        resumed = await controller(app).send_thread_message(
            source_thread_id=source.thread_id, thread_id=result["thread_id"], message="After restart"
        )
        receipt = resumed["receipt"]["receipt_id"]
        assert (await app.wait_root_operation(receipt)).status is RootOperationStatus.completed
        assert (await app.inspect_operation_configuration(receipt)).agent.model_id == expected_model
        ordinary = await controller(app).send_thread_message(
            source_thread_id=result["thread_id"], thread_id=source.thread_id, message="Return report"
        )
        receipt = ordinary["receipt"]["receipt_id"]
        assert (await app.wait_root_operation(receipt)).status is RootOperationStatus.completed
        assert (await app.inspect_operation_configuration(receipt)).agent.model_id == "model-primary"


async def test_terminal_does_not_receive_sidekick_instructions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = configuration(tmp_path)
    document = yaml.safe_load(root.read_text())
    document["webui"] = {"sidekick": {"model": "model-secondary"}}
    root.write_text(yaml.safe_dump(document))
    seen = []

    async def resolve(self, context, model_id):
        async def model(messages, info):
            seen.append(info)
            yield "Completed"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Work locally")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
    assert "Sidekick is enabled" not in (seen[0].instructions or "")
    assert "list_agents" not in {tool.name for tool in seen[0].function_tools}


async def test_worker_can_ask_requester_receive_answer_and_report_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = configuration(tmp_path)
    requester_id = ""
    worker_id = ""
    worker_idle, requester_idle = Event(), Event()
    steps: dict[str, int] = {}
    returned: dict[str, dict] = {}

    async def resolve(self, context, model_id):
        thread_id = context.deps.thread_id

        async def model(messages, info):
            nonlocal worker_id
            step = steps.get(thread_id, 0)
            steps[thread_id] = step + 1
            for message in messages:
                for part in message.parts:
                    if isinstance(part, ToolReturnPart):
                        returned[part.tool_call_id] = part.content
            if thread_id == requester_id:
                if step == 0:
                    assert "Which format should I use?" in str(messages)
                    await worker_idle.wait()
                    target, text, call = worker_id, "Use Markdown.", "answer"
                else:
                    yield "Integrated report" if step == 2 else "Answer sent"
                    return
            else:
                worker_id = thread_id
                if step == 0:
                    assert "Requesting Project: project-main" in str(messages)
                    assert f"send_thread_message(thread_id={requester_id!r}" in str(messages)
                    target, text, call = requester_id, "Which format should I use?", "question"
                elif step == 2:
                    assert "Use Markdown." in str(messages)
                    await requester_idle.wait()
                    target, text, call = requester_id, "Finished the Markdown report; validation passed.", "report"
                else:
                    yield "Message sent"
                    return
            yield {
                0: DeltaToolCall(
                    name="send_thread_message",
                    json_args=json.dumps({"thread_id": target, "message": text}),
                    tool_call_id=call,
                )
            }

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui", instrumentation=None) as app:
        requester_id = (await app.create_thread()).thread_id
        with fail_after(15):
            worker = await controller(app).create_thread(
                source_thread_id=requester_id, prompt="Prepare a report", title=None, agent_id=None
            )
            assert (
                await app.wait_root_operation(worker["receipt"]["receipt_id"])
            ).status is RootOperationStatus.completed
            worker_idle.set()
            assert (
                await app.wait_root_operation(returned["question"]["receipt"]["receipt_id"])
            ).status is RootOperationStatus.completed
            requester_idle.set()
            assert (
                await app.wait_root_operation(returned["answer"]["receipt"]["receipt_id"])
            ).status is RootOperationStatus.completed
            assert (
                await app.wait_root_operation(returned["report"]["receipt"]["receipt_id"])
            ).status is RootOperationStatus.completed
        assert all(result["ok"] for result in returned.values())
        requester_history = (await app.get_thread_transcript(thread_id=requester_id)).model_dump_json()
        worker_history = (await app.get_thread_transcript(thread_id=worker_id)).model_dump_json()
        assert "Which format should I use?" in requester_history and "Integrated report" in requester_history
        assert "Use Markdown." in worker_history and "validation passed" in worker_history


@pytest.mark.parametrize("project_id", [None, "project-captured"])
async def test_requester_identity_uses_run_capture_not_future_thread_selections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, project_id: str | None
) -> None:
    root = configuration(tmp_path)
    captured = SimpleNamespace(project_id=project_id, project_roots=("/captured/root",), webui_sidekick=None)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        source = await app.create_thread()
        prompts = []

        async def submit(**kwargs):
            prompts.append(kwargs["prompt"])
            raise ThreadError("Admission rejected", code="test_rejected")

        monkeypatch.setattr(app._root_runs, "submit_prompt", submit)
        capability = ThreadCollaborationCapability(
            controller=controller(app), source_thread_id=source.thread_id, composition=captured
        )
        context = SimpleNamespace(deps=SimpleNamespace(thread_id=source.thread_id))
        instructions = capability.get_instructions()
        assert f"Your current Thread: {source.thread_id}" in instructions
        assert f"Your captured Project: {project_id or 'No Project'}" in instructions
        assert "/captured/root" in instructions
        result = await capability.create_thread(context, prompt="Independent work")
        assert result["ok"] is False and result["thread_id"] != source.thread_id
        context, body = prompts[0]
        assert context.metadata == {"display": False}
        assert f"Requesting Project: {project_id or 'No Project'}" in context.content
        assert "Requesting Project: project-main" not in context.content
        assert body.content == "Independent work"
        assert body.metadata["harness_ui"]["thread_message"]["source_thread_id"] == source.thread_id


@pytest.mark.parametrize("delivery", ["run", "send", "steer"])
async def test_follow_up_input_preserves_attribution_through_execution_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delivery: str
) -> None:
    root = configuration(tmp_path)
    started, release = Event(), Event()

    async def resolve(self, context, model_id):
        async def model(messages, info):
            if "Focus here" in str(messages):
                assert "Message from Thread" in str(messages)
                yield "Applied the feedback"
            else:
                yield "Working"
                started.set()
                await release.wait()

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui", instrumentation=None) as app:
        source = await app.create_thread(title="Review source")
        target = await app.create_thread()
        tools = controller(app)
        with fail_after(15):
            if delivery == "run":
                receipt = await tools.run_thread(
                    source_thread_id=source.thread_id, thread_id=target.thread_id, prompt="Focus here"
                )
                receipt_id = receipt["receipt_id"]
            else:
                receipt = await app.submit_thread(thread_id=target.thread_id, prompt="Start work")
                receipt_id = receipt.receipt_id
                await started.wait()
                send = tools.send_thread_message if delivery == "send" else tools.steer_thread
                result = await send(source_thread_id=source.thread_id, thread_id=target.thread_id, message="Focus here")
                assert result["accepted"] is True
                assert result["receipt_id"] == receipt_id
                release.set()
            operation = await app.wait_root_operation(receipt_id)
            assert operation.status is RootOperationStatus.completed, operation.model_dump_json()
        history = await app.get_thread_transcript(thread_id=target.thread_id)
        parts = [part for entry in history.entries for part in entry.parts]
        context = next(part for part in parts if (part.text or "").startswith("Message from Thread"))
        assert context.metadata.display is False
        body = next(part for part in parts if part.text == "Focus here")
        assert body.metadata.model_dump()["harness_ui"]["thread_message"] == {
            "source_thread_id": source.thread_id,
            "source_thread_title": "Review source",
        }
        assert body.metadata.display is True
        assert any(part.text == "Applied the feedback" for part in parts)


@pytest.mark.parametrize("action", ["run", "steer"])
async def test_hidden_context_does_not_admit_empty_cross_thread_input(tmp_path: Path, action: str) -> None:
    root = configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        source = await app.create_thread()
        target = await app.create_thread()
        tools = controller(app)
        with pytest.raises(ThreadError, match="non-empty"):
            if action == "run":
                await tools.run_thread(source_thread_id=source.thread_id, thread_id=target.thread_id, prompt=" \n")
            else:
                await tools.steer_thread(source_thread_id=source.thread_id, thread_id=target.thread_id, message=" \n")
        assert await app._root_runs.active(target.thread_id) is None
