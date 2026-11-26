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

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


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
    document = yaml.safe_load(root.read_text())
    document["subagents"] = {
        "dynamic": {"allowed_models": ["model-primary", "model-secondary"], "default_model": "model-secondary"}
    }
    root.write_text(yaml.safe_dump(document))
    return root


def controller(app) -> ThreadToolController:
    return ThreadToolController(
        threads=app._store.threads,
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
        listed = await tools.list_threads(
            source_thread_id=source.thread_id, query=None, cursor=None, limit=20, project_id="project-main"
        )
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
        created = await capability.create_thread(ctx, prompt="Work", model=model_id)
        assert created["ok"] is False and created["error"]["code"] == "creation_model_not_allowed"
        assert (await app.list_threads()).total == 2
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
            TextContent(
                f"Message from Thread {source.thread_id}:",
                metadata={"display": False, "source_id": "a13n-harness-ui.thread-message-context"},
            ),
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
    root = configuration(tmp_path)
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
        # The native output policy can wrap this inspection as guidance grows.
        own = own.get("result", own)
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
    assert any("Agent creation models:" in text for text in instructions)


async def test_creation_defaults_survive_restart_and_legacy_run_snapshots(tmp_path):
    root = configuration(tmp_path)
    settings = _settings(tmp_path / "data")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        source = await app.create_thread()
        run = await app._root_runs._executor.capture(thread_id=source.thread_id, prompt="Work")
        old = run.published.value.model_dump(mode="json")
        old["webui_sidekick"] = {"agent": "old-agent", "model": "old-model"}
        assert ResolvedRunComposition.model_validate(old) == run.published.value
        created = await controller(app).create_thread(
            source_thread_id=source.thread_id,
            prompt="First task",
            title=None,
            agent_id=None,
            model_id="model-primary",
            source_composition=run.published.value,
        )
        await app.wait_root_operation(created["receipt"]["receipt_id"])
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        target = (await app.get_thread(created["thread_id"])).thread
        assert target.configuration.default_model_id == "model-primary" and target.role == "ordinary"
        sent = await controller(app).send_thread_message(
            source_thread_id=source.thread_id, thread_id=target.thread_id, message="Follow up"
        )
        receipt = sent["receipt"]["receipt_id"]
        assert (await app.wait_root_operation(receipt)).status is RootOperationStatus.completed
        assert (await app.inspect_operation_configuration(receipt)).agent.model_id == "model-primary"


async def test_create_thread_validates_thinking_against_current_generation_before_creation(tmp_path: Path) -> None:
    root = configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        source = await app.create_thread()
        captured = await app._root_runs._executor.capture(thread_id=source.thread_id, prompt="Start")
        # Edit the allowed creation Model route to one without reasoning support while the creator Run is captured.
        secondary = tmp_path / "models/secondary.yaml"
        updated = yaml.safe_load(secondary.read_text())
        updated["route"] = "openai:gpt-4o"
        secondary.write_text(yaml.safe_dump(updated))
        await app.reload_configuration()
        capability = ThreadCollaborationCapability(
            controller=controller(app), source_thread_id=source.thread_id, composition=captured.published.value
        )
        ctx = SimpleNamespace(deps=SimpleNamespace(thread_id=source.thread_id))
        result = await capability.create_thread(ctx, prompt="Work", model="model-secondary", thinking="high")
        assert result["ok"] is False
        assert result["error"]["code"] == "thinking_selection_invalid"
        # Validation must fail before allocating an orphan Thread.
        assert (await app.list_threads()).total == 1


async def test_cross_project_create_uses_target_project_default_agent_and_current_inherits_captured(
    tmp_path: Path,
) -> None:
    from a13n_harness_ui.surfaces import ThreadConfigurationMutationInput, ThreadConfigurationPatch

    root = configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        source = await app.create_thread()
        captured = await app._root_runs._executor.capture(thread_id=source.thread_id, prompt="Start")
        tools = controller(app)
        # Explicit destination Project without agent_id resolves that Project's default Agent.
        other = await tools.create_thread(
            source_thread_id=source.thread_id,
            prompt="Other project task",
            title=None,
            agent_id=None,
            project_id="project-other",
            source_composition=captured.published.value,
        )
        assert other["ok"] is True
        other_thread = (await app.get_thread(other["thread_id"])).thread
        assert other_thread.configuration.project_id == "project-other"
        assert other_thread.configuration.agent_source.id == "agent-worker"
        assert (await app.wait_root_operation(other["receipt"]["receipt_id"])).status is RootOperationStatus.completed
        # With project_id="current", editing source's sticky agent mid-Run still inherits the captured Agent & roots.
        await app.patch_thread_configuration(
            thread_id=source.thread_id,
            mutation=ThreadConfigurationMutationInput(
                expected_version=1, patch=ThreadConfigurationPatch(agent_id="agent-worker")
            ),
        )
        same = await tools.create_thread(
            source_thread_id=source.thread_id,
            prompt="Same project task",
            title=None,
            agent_id=None,
            project_id="current",
            source_composition=captured.published.value,
        )
        assert same["ok"] is True
        same_thread = (await app.get_thread(same["thread_id"])).thread
        assert same_thread.configuration.project_id == "project-main"
        assert same_thread.configuration.agent_source.id == "agent-assistant"
        assert same_thread.configuration.local_roots == source.configuration.local_roots
        assert (await app.wait_root_operation(same["receipt"]["receipt_id"])).status is RootOperationStatus.completed


async def test_terminal_does_not_receive_creation_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = configuration(tmp_path)
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
    assert "Agent creation models:" not in (seen[0].instructions or "")
    assert "list_agents" not in {tool.name for tool in seen[0].function_tools}


@pytest.mark.parametrize("coordinator", [False, True])
async def test_worker_can_ask_requester_receive_answer_and_report_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, coordinator: bool
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
                if coordinator:
                    assert "You are a Coordinator in this Project" in info.instructions
                    assert "At the start of each Run" in info.instructions
                    assert "get_thread(thread_id=...)" in info.instructions
                    assert "compact coordination note" in info.instructions
                    assert "Before summarize, reconcile tasks and notes" in info.instructions
                    assert "no background polling" in info.instructions
                    names = {tool.name for tool in info.function_tools}
                    assert "create_worker" in names and "create_thread" not in names
                if step == 0:
                    assert "Which format should I use?" in str(messages)
                    await worker_idle.wait()
                    target, text, call = worker_id, "Use Markdown.", "answer"
                else:
                    yield "Integrated report" if step == 2 else "Answer sent"
                    return
            else:
                worker_id = thread_id
                if coordinator:
                    assert self._recipes[model_id].model_id == "model-primary"
                    assert "You are a Coordinator in this Project" not in info.instructions
                    assert "requester is the Coordinator" in str(messages)
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
        # Explicit question/report protocol is independent of best-effort lifecycle notices.
        monkeypatch.setattr(app._root_runs, "_on_settled", None)
        requester = await app.create_thread()
        if coordinator:
            requester = await app.promote_coordinator(requester.thread_id)
        requester_id = requester.thread_id
        admission = await app._root_runs._executor.capture(thread_id=requester_id, prompt="Coordinate")
        with fail_after(15):
            worker = await controller(app).create_thread(
                worker=coordinator,
                source_thread_id=requester_id,
                prompt="Prepare a report",
                title=None,
                agent_id=None,
                source_composition=admission.published.value,
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
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        source = await app.create_thread()
        actual = await app._root_runs._executor.capture(thread_id=source.thread_id, prompt="Prepare")
        captured = actual.published.value.model_copy(
            update={"project_id": project_id, "project_roots": ("/captured/root",)}
        )
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
        assert context.metadata == {"display": False, "source_id": "a13n-harness-ui.thread-message-context"}
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
                receipt = await tools.send_thread_message(
                    source_thread_id=source.thread_id, thread_id=target.thread_id, message="Focus here"
                )
                receipt_id = receipt["receipt"]["receipt_id"]
            else:
                receipt = await app.submit_thread(thread_id=target.thread_id, prompt="Start work")
                receipt_id = receipt.receipt_id
                await started.wait()
                result = await tools.send_thread_message(
                    source_thread_id=source.thread_id, thread_id=target.thread_id, message="Focus here"
                )
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


async def test_hidden_context_does_not_admit_empty_cross_thread_input(tmp_path: Path) -> None:
    root = configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        source = await app.create_thread()
        target = await app.create_thread()
        capability = ThreadCollaborationCapability(controller=controller(app), source_thread_id=source.thread_id)
        result = await capability.send_thread_message(
            SimpleNamespace(deps=SimpleNamespace(thread_id=source.thread_id)), target.thread_id, " "
        )
        assert result["error"]["code"] == "thread_message_empty"
        assert await app._root_runs.active(target.thread_id) is None
