"""Terminal cancellation, pending decisions, questions and Goal status through the real App."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.backend import SessionBackend
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.settings import EnvdRuntimeSettings, HarnessUiSettings, StorageSettings
from pydantic_ai.models.function import FunctionModel

from .test_interactive import _seed, no_real_model_requests  # noqa: F401  (autouse guard)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["normal", "goal"])
async def test_cancel_is_receipt_owned_and_session_is_reusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode
) -> None:
    import a13n_harness.models.codex as runtime

    path = await _seed(tmp_path, monkeypatch)
    entered = asyncio.Event()

    async def stream(messages, info):
        yield "Started"
        entered.set()
        await asyncio.sleep(60)
        yield "Never reached"

    monkeypatch.setattr(runtime, "CodexRequestModel", lambda *args, **kwargs: FunctionModel(stream_function=stream))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        renderer = StreamRenderer(backend.status)
        job = asyncio.create_task(backend.execute(renderer, prompt="Wait", mode=mode))
        await asyncio.wait_for(entered.wait(), timeout=5)
        await backend.cancel()
        result = await asyncio.wait_for(job, timeout=5)
        assert "cancel" in result.lower()
        assert "Never reached" not in renderer.drain()
        assert await app.active_root_operation(backend.thread_id) is None
        if mode == "goal":
            assert backend.status.goal.status == "cancelled"
            assert (await app.get_thread(backend.thread_id)).thread.goal.status == "cancelled"


@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["approve", "deny"])
@pytest.mark.parametrize("mode", ["full-control", "sandbox"])
@pytest.mark.parametrize("shortcut", [True, False])
async def test_pending_shell_decision_is_reviewed_and_resumed_through_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision: str, mode: str, shortcut: bool
) -> None:
    if mode == "sandbox" and os.environ.get("A13N_HARNESS_UI_TEST_SANDBOX") != "1":
        pytest.skip("Set A13N_HARNESS_UI_TEST_SANDBOX=1 with a compatible envd binary and native isolation support")
    import a13n_harness.models.codex as runtime
    import yaml
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall

    path = await _seed(tmp_path, monkeypatch)
    agent_path = path.parent / "agents/codex.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    if shortcut:
        root = yaml.safe_load(path.read_text())
        root["security"] = {"shell_review": {"enable": True, "model": "model-codex", "risk_threshold": "high"}}
        path.write_text(yaml.safe_dump(root))
    else:
        # The disabled root shortcut must not suppress explicit Agent policies.
        agent["capabilities"].extend(
            [
                {
                    "capability": "ToolPermissionsCapability",
                    "configuration": {
                        "rules": {"environment.shell_exec": "review"},
                        "review": {
                            "model": "model-codex",
                            "risk_threshold": "high",
                            "on_flagged": "approval_required",
                            "on_error": "allow",
                        },
                    },
                },
            ]
        )
    agent_path.write_text(yaml.safe_dump(agent))
    marker = tmp_path / "approved-result"

    async def stream(messages, info):
        if not info.function_tools:
            yield {
                0: DeltaToolCall(name=info.output_tools[0].name, json_args='{"risk":"high","reason":"Needs review"}')
            }
        elif any(
            isinstance(message, ModelRequest) and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        ):
            yield "Decision handled."
        else:
            yield {
                0: DeltaToolCall(
                    name="shell_exec",
                    json_args=json.dumps({"command": "echo reviewed > approved-result"}),
                    tool_call_id="approval-one",
                )
            }

    monkeypatch.setattr(
        runtime,
        "CodexRequestModel",
        lambda *args, **kwargs: FunctionModel(stream_function=stream, profile={"supports_json_object_output": True}),
    )
    envd = EnvdRuntimeSettings(executable=Path(os.environ["A13N_ENVD_EXECUTABLE"]) if mode == "sandbox" else None)
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), envd_runtime=envd),
        configuration_path=path,
    ) as app:
        backend = SessionBackend(app, CliRequest(environment_mode=mode), tmp_path, Status())
        first = await backend.execute(StreamRenderer(backend.status), prompt="Try reviewed command")
        assert "Pending decisions" in first
        assert "/approve" not in first and "/result" not in first
        assert not marker.exists()
        review = await backend.review("approval-one")
        assert "shell_exec" in review
        assert "approved-result" in review
        interaction = await backend.interaction()
        assert interaction is not None
        prompt = interaction.prompt()
        assert "Reason: Needs review" in prompt
        assert "Risk: high" in prompt  # Shell presentation reads the shared risk assessment.
        assert prompt.index("Reason:") < prompt.index("Command:")
        assert "Command:\necho reviewed > approved-result" in prompt
        response = interaction.accept("yes" if decision == "approve" else "no")
        assert response is not None and not isinstance(response, str)
        assert await backend.execute(StreamRenderer(backend.status), response=response) == ""
        assert marker.exists() is (decision == "approve"), (
            await app.get_thread_transcript(thread_id=backend.thread_id)
        ).model_dump_json()
        assert await backend.pending() == ""


@pytest.mark.anyio
@pytest.mark.parametrize("timeout", [False, True])
@pytest.mark.parametrize("mode", ["normal", "goal"])
async def test_default_tasks_and_questions_suspend_resume_through_native_ui(
    tmp_path: Path, monkeypatch, timeout: bool, mode
) -> None:
    import a13n_harness.models.codex as runtime
    import yaml
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall

    path = await _seed(tmp_path, monkeypatch)
    root = yaml.safe_load(path.read_text())
    root["tools"]["interaction_timeout_seconds"] = 30
    path.write_text(yaml.safe_dump(root))

    async def stream(messages, info):
        names = {tool.name for tool in info.function_tools}
        assert {"task_create", "task_update", "task_list", "ask_user_question"} <= names
        assert {"run_code", "run_program", "store", "load", "forget"} <= names
        assert "note" not in names
        returned = [
            part.tool_name
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if "task_create" not in returned:
            yield {
                0: DeltaToolCall(
                    name="task_create",
                    tool_call_id="create-task",
                    json_args=json.dumps(
                        {"subject": "Choose an option", "description": "Exercise default native tools"}
                    ),
                )
            }
        elif "ask_user_question" not in returned:
            yield {
                0: DeltaToolCall(
                    name="ask_user_question",
                    tool_call_id="question-one",
                    json_args=json.dumps(
                        {
                            "questions": [
                                {
                                    "header": "Option",
                                    "question": "Which option?",
                                    "options": [
                                        {"label": "One", "description": "First"},
                                        {"label": "Two", "description": "Second"},
                                    ],
                                }
                            ]
                        }
                    ),
                )
            }
        else:
            if timeout:
                assert "No answer or approval was provided" in str(messages)
            yield "Choice applied.\n[GOAL_COMPLETE]" if mode == "goal" else "Choice applied."

    monkeypatch.setattr(runtime, "CodexRequestModel", lambda *args, **kwargs: FunctionModel(stream_function=stream))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        renderer = StreamRenderer(backend.status)
        assert await backend.execute(renderer, prompt="Create a task and ask", mode=mode) == ""
        if mode == "goal":
            assert backend.status.goal.status == "suspended"
            assert backend.status.goal.iteration == 0
            assert backend.status.goal.objective == "Create a task and ask"
        assert len(renderer.tasks.tasks) == 1
        assert len((await app.thread_tasks(thread_id=backend.thread_id)).tasks) == 1
        interaction = await backend.interaction()
        assert interaction is not None and interaction.title() == "Option"
        assert interaction.timeout_seconds == 30
        if timeout:
            interaction.request_started -= interaction.timeout_seconds
            assert interaction.expired
            response = interaction.expire()
        else:
            response = interaction.accept("One")
        assert response is not None and not isinstance(response, str)
        assert await backend.execute(renderer, response=response) == ""
        assert "Choice applied" in "".join(block.source for block in renderer.transcript.blocks.values())
        if mode == "goal":
            assert backend.status.goal.status == "verified"
            assert backend.status.goal.iteration == 0
            assert (await app.get_thread(backend.thread_id)).thread.goal == backend.status.goal


@pytest.mark.anyio
async def test_goal_live_status_and_saved_outcome_share_app_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    import a13n_harness.models.codex as runtime

    path = await _seed(tmp_path, monkeypatch)
    waiting, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield "There is still work to verify."
        else:
            waiting.set()
            await release.wait()
            yield "All requirements checked.\n[GOAL_COMPLETE]"

    monkeypatch.setattr(runtime, "CodexRequestModel", lambda *args, **kwargs: FunctionModel(stream_function=stream))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        status = Status(state="working")
        backend = SessionBackend(app, CliRequest(), tmp_path, status)
        task = asyncio.create_task(
            backend.execute(StreamRenderer(status), prompt="Verify the entire task", mode="goal")
        )
        try:
            await asyncio.wait_for(waiting.wait(), 10)
            await backend.refresh_goal()
            assert not task.done()
            assert status.goal.iteration == 1
            assert "Goal checking 1/10" in status.line(80)
            assert status.line(30).strip().startswith("Goal")
            receipt = backend.receipt_id
            assert (await app.get_root_operation(receipt)).goal.iteration == 1
        finally:
            release.set()
            await asyncio.wait_for(task, 10)
        assert calls == 2
        assert (await app.get_root_operation(receipt)).goal.status == "verified"
        thread_id = backend.thread_id
        assert (await app.get_thread(thread_id)).thread.goal.status == "verified"
        assert "Goal verified 1/10" in status.line(80)
        await backend.new()
        assert status.goal is None
        await backend.resume(thread_id)
        assert status.goal.status == "verified"
        await backend.execute(StreamRenderer(status), prompt="An ordinary new task")
        assert status.goal is None
        assert (await app.get_thread(thread_id)).thread.goal is None
