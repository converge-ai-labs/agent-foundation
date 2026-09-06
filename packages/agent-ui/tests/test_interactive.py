from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from a13n_ui.app import open_agent_ui_app
from a13n_ui.cli import CliRequest
from a13n_ui.composition import AgentCompositionResolver
from a13n_ui.configuration import load_agent_ui_configuration
from a13n_ui.configuration.setup import SetupSelection, preview_setup, publish_setup
from a13n_ui.extensions import AgentUiExtensionCatalog
from a13n_ui.interactive.backend import SessionBackend
from a13n_ui.interactive.commands import Command, CommandRegistry
from a13n_ui.interactive.rendering import Status, StreamRenderer, terminal_text
from a13n_ui.interactive.setup import SetupWizard
from a13n_ui.settings import AgentUiSettings, EnvdRuntimeSettings, StorageSettings
from pydantic_ai.models.function import FunctionModel


@pytest.fixture(autouse=True)
def no_real_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    import pydantic_ai.models

    monkeypatch.setattr(pydantic_ai.models, "ALLOW_MODEL_REQUESTS", False)


def test_command_registry_has_one_grammar_and_rejects_collisions() -> None:
    registry = CommandRegistry()
    assert registry.parse("/exit").command.name == "quit"
    assert registry.parse("/mode detailed", busy=True).arguments == ("detailed",)
    assert registry.completions("/mo")[0][0] == "/mode"
    assert registry.completions("/mode d")[0][0] == "detailed"
    assert "Alt+Enter" in registry.help()
    for invalid in ("/unknown", "/model a b", "/mode invalid", '/attach "unclosed'):
        with pytest.raises(ValueError):
            registry.parse(invalid)
    with pytest.raises(ValueError, match="unavailable"):
        registry.parse("/thinking high", busy=True)
    with pytest.raises(ValueError, match="Duplicate"):
        CommandRegistry((Command("one", "one", aliases=("two",)), Command("two", "two")))


def test_renderer_modes_switch_without_replay_and_preserve_control_safety() -> None:
    status = Status()
    renderer = StreamRenderer(status)
    renderer.ingest("THINKING_TEXT_MESSAGE_CONTENT", {"delta": "hidden summary"})
    renderer.ingest("TEXT_MESSAGE_CONTENT", {"delta": "Hello "})
    assert renderer.drain() == "Hello "
    assert renderer.drain() == ""
    status.mode = "detailed"
    renderer.ingest("REASONING_MESSAGE_CONTENT", {"delta": "public reasoning"})
    renderer.ingest("TOOL_CALL_START", {"tool_call_id": "edit-1", "tool_call_name": "edit"})
    renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "edit-1", "delta": '{"file_path":"a.py"}'})
    renderer.ingest("TOOL_CALL_END", {"tool_call_id": "edit-1"})
    renderer.ingest("TOOL_CALL_RESULT", {"tool_call_id": "edit-1", "content": "edited"})
    result = renderer.drain()
    assert "public reasoning" in result and "a.py" in result and "edited" in result
    assert "hidden summary" not in result
    assert "\x1b" not in terminal_text("unsafe\x1b]52;c;YQ==\x07")
    renderer.ingest("TEXT_MESSAGE_CONTENT", None)
    assert renderer.gap


def test_stream_argument_and_result_memory_is_bounded() -> None:
    renderer = StreamRenderer(Status(mode="detailed"), limit=128)
    renderer.ingest("TOOL_CALL_START", {"tool_call_id": "one", "tool_call_name": "write"})
    for _ in range(1000):
        renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "one", "delta": "x" * 1000})
    renderer.ingest("TOOL_CALL_END", {"tool_call_id": "one"})
    renderer.ingest("TOOL_CALL_RESULT", {"content": "\n".join(["line"] * 100)})
    assert len(renderer.drain()) < 400


def test_tool_streams_are_correlated_bounded_and_do_not_override_root_cancellation() -> None:
    status = Status(mode="detailed")
    renderer = StreamRenderer(status, limit=128)
    for run_id in ("child-a", "child-b"):
        renderer.ingest("TOOL_CALL_START", {"tool_call_id": "one", "tool_call_name": "edit"}, child=True, run_id=run_id)
        renderer.ingest("TOOL_CALL_END", {"tool_call_id": "one"}, child=True, run_id=run_id)
    for run_id in ("child-b", "child-a"):
        renderer.ingest("TOOL_CALL_RESULT", {"tool_call_id": "one", "content": "done"}, child=True, run_id=run_id)
    result = renderer.drain()
    assert "[Result] edit · child-a / one" in result
    assert "[Result] edit · child-b / one" in result
    for index in range(256):
        renderer.ingest("TOOL_CALL_START", {"tool_call_id": str(index), "tool_call_name": "edit"})
    assert len(renderer._tools) == 128
    renderer.ingest("TEXT_MESSAGE_CONTENT", {"delta": "answer"})
    assert status.state == "responding"
    status.state = "cancelling"
    renderer.ingest("TOOL_CALL_START", {"tool_call_id": "late", "tool_call_name": "edit"})
    renderer.ingest("TEXT_MESSAGE_CONTENT", {"delta": "late answer"})
    assert status.state == "cancelling"


def test_setup_choices_expand_to_explicit_native_context_values() -> None:
    wizard = SetupWizard()
    for value in ("", "", "", "extended", "medium", "sandbox", "no", ""):
        wizard.accept(value)
    assert wizard.question is None
    selection = wizard.selection("/tmp")
    assert selection["codex_model"] == "gpt-5.6-sol"
    assert selection["codex_context_window"] == 872000
    assert selection["proactive_context_management_threshold"] == 0.65
    assert selection["compact_threshold"] == 0.9
    assert selection["environment_profile"] == "environment-sandbox"
    assert selection["codex_thinking"] == "medium"


@pytest.mark.parametrize("arguments", [["--help"], ["--version"], ["auth", "login", "--help"], ["run", "--help"]])
def test_cli_help_never_imports_runtime(arguments: list[str], tmp_path: Path) -> None:
    script = f"""
import sys
from a13n_ui.cli import main
main({arguments!r})
for prefix in ('a13n_harness', 'pydantic', 'sqlalchemy', 'textual', 'fastapi', 'prompt_toolkit', 'a13n_ui.app'):
    assert not any(name == prefix or name.startswith(prefix + '.') for name in sys.modules), prefix
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / ".a13n-ui").exists()


async def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    (tmp_path / "codex").mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = tmp_path / "config" / "a13n-ui.yaml"
    selection = SetupSelection(
        providers=("codex",),
        default_agent="agent-codex",
        project_path=str(tmp_path),
        environment_profile="environment-native",
        shell_review=False,
    )
    validate = AgentCompositionResolver(AgentUiExtensionCatalog()).validate_generation
    preview = await preview_setup(path, selection, validate_candidate=validate)
    result = await publish_setup(path, selection, expected_generation=preview.generation, validate_candidate=validate)
    assert result.completed
    return path


@pytest.mark.anyio
async def test_exact_cwd_workspace_never_retargets_saved_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = await _seed(tmp_path, monkeypatch)
    nested = tmp_path / "nested"
    nested.mkdir()
    async with open_agent_ui_app(
        AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        first = await app.ensure_cwd_workspace(nested)
        second = await app.ensure_cwd_workspace(nested)
        assert first == second
        assert first.project_id != "project-local"
        configuration = await app.current_configuration()
        assert configuration.projects["project-local"].roots[0].path == str(tmp_path)
        assert configuration.projects[first.project_id].roots[0].path == str(nested)
        backend = SessionBackend(app, CliRequest(), nested, Status())
        thread_id = await backend.ensure_session()
        other = SessionBackend(app, CliRequest(), tmp_path, Status())
        with pytest.raises(ValueError, match="another workspace"):
            await other.resume(thread_id)
        assert thread_id in await backend.resume()


@pytest.mark.anyio
async def test_session_overrides_capture_native_context_and_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_ui.model_runtime as runtime

    path = await _seed(tmp_path, monkeypatch)
    before = (path.parent / "models/codex.yaml").read_bytes()
    efforts = []

    async def stream(messages, info):
        efforts.append(info.model_request_parameters.thinking)
        yield "Hello "
        yield "from the mock."

    monkeypatch.setattr(
        runtime,
        "build_codex_model",
        lambda *args, **kwargs: FunctionModel(stream_function=stream, profile={"supports_thinking": True}),
    )
    async with open_agent_ui_app(
        AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        status = Status()
        backend = SessionBackend(app, CliRequest(), tmp_path, status)
        assert await backend.initialize()
        assert status.context_window == 350000
        await backend.thinking("low")
        renderer = StreamRenderer(status)
        assert await backend.execute(renderer, prompt="First") == ""
        assert "Hello from the mock." in renderer.drain()
        thread_id = backend.thread_id
        usage = await app.context_usage(thread_id)
        assert usage.thinking == "low"
        assert usage.context_window == 350000
        assert usage.latest_request_tokens is not None
        await backend.thinking("medium")
        assert await backend.execute(StreamRenderer(status), prompt="Second") == ""
        assert (await app.context_usage(thread_id)).thinking == "medium"
        resumed = SessionBackend(app, CliRequest(thread_id=thread_id), tmp_path, Status())
        await resumed.initialize()
        assert resumed.overrides.thinking == "medium"
        assert "First" in await resumed.history()
        assert (await app.get_thread(thread_id)).thread.configuration.version == 1
        from a13n_ui.cli_runtime import _run_one_shot

        monkeypatch.chdir(tmp_path)
        assert await _run_one_shot(app, CliRequest(command="run", thread_id=thread_id, prompt="One-shot resume")) == 0
    assert efforts == ["low", "medium", "medium"]
    assert (path.parent / "models/codex.yaml").read_bytes() == before
    config = await load_agent_ui_configuration(path)
    assert config.models["model-codex"].settings["thinking"] == "high"


@pytest.mark.anyio
async def test_cancel_is_receipt_owned_and_session_is_reusable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_ui.model_runtime as runtime

    path = await _seed(tmp_path, monkeypatch)
    entered = asyncio.Event()

    async def stream(messages, info):
        yield "Started"
        entered.set()
        await asyncio.sleep(60)
        yield "Never reached"

    monkeypatch.setattr(runtime, "build_codex_model", lambda *args, **kwargs: FunctionModel(stream_function=stream))
    async with open_agent_ui_app(
        AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        renderer = StreamRenderer(backend.status)
        job = asyncio.create_task(backend.execute(renderer, prompt="Wait"))
        await asyncio.wait_for(entered.wait(), timeout=5)
        await backend.cancel()
        result = await asyncio.wait_for(job, timeout=5)
        assert "cancel" in result.lower()
        assert "Never reached" not in renderer.drain()
        assert await app.active_root_operation(backend.thread_id) is None


@pytest.mark.anyio
async def test_terminal_flush_retains_semantic_output_independently_of_draw() -> None:
    from a13n_ui.interactive.shell import CliShell
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.output import DummyOutput

    with create_app_session(input=DummyInput(), output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.renderer.append("FINAL")
        await shell.flush()
        assert shell.renderer.drain() == ""
        assert [block.source for block in shell.renderer.transcript.blocks.values()] == ["FINAL"]


@pytest.mark.anyio
async def test_native_compaction_is_active_not_only_written_to_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_ui.model_runtime as runtime
    import yaml
    from pydantic_ai.messages import ModelResponse, TextPart

    path = await _seed(tmp_path, monkeypatch)
    agent_path = path.parent / "agents/codex.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    for capability in agent["capabilities"]:
        if capability["capability"] == "compaction":
            capability["configuration"] = {"trigger_tokens": 1}
    agent_path.write_text(yaml.safe_dump(agent))
    calls = []

    def reply(messages, info):
        calls.append(messages)
        return ModelResponse(parts=[TextPart("Mock context summary and answer.")])

    async def stream_reply(messages, info):
        calls.append(messages)
        yield "Mock context summary and answer."

    monkeypatch.setattr(
        runtime,
        "build_codex_model",
        lambda *args, **kwargs: FunctionModel(function=reply, stream_function=stream_reply),
    )
    async with open_agent_ui_app(
        AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        first = await backend.execute(StreamRenderer(backend.status), prompt="First request")
        assert first == ""
        second_renderer = StreamRenderer(backend.status)
        second = await backend.execute(second_renderer, prompt="Continue")
        assert second == ""
        assert len(calls) == 3, "Two foreground requests plus one same-agent compaction request"
        history = await backend.history()
        assert "Mock context summary" in history


def test_context_samples_replace_root_requests_without_double_counting_cache_or_children() -> None:
    from datetime import UTC, datetime

    from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord
    from a13n_ui.live import LiveEvent, root_context_samples

    root = ModelUsageRecord(
        record_id="root-1",
        run_id="run-1",
        response_ordinal=2,
        agent_instance_id="agent-root",
        response_state="complete",
        response_timestamp=datetime.now(UTC),
        request_usage=BoundedRequestUsage(input_tokens=100, cache_read_tokens=80, output_tokens=20),
        cost_source="unknown",
        pricing_status="disabled",
    )
    child = root.model_copy(update={"record_id": "child-1", "parent_agent_instance_id": "agent-root"})
    delegated = root.model_copy(update={"record_id": "delegated-1", "delegation_id": "delegation-1"})
    event = LiveEvent(
        epoch="test",
        sequence=1,
        run_kind="root",
        root_thread_id="thread-1",
        thread_id="thread-1",
        run_id="run-1",
        event_type="CUSTOM",
        payload_omitted=False,
        payload={
            "value": {
                "event": {
                    "payload": {
                        "type": "usage_report",
                        "records": [item.model_dump(mode="json") for item in (root, child, delegated)],
                    }
                }
            }
        },
    )
    samples = root_context_samples(event)
    assert len(samples) == 1
    assert samples[0].tokens == 120
    assert samples[0].response_ordinal == 2
    assert root_context_samples(event.model_copy(update={"run_kind": "child"})) == ()


def test_resume_with_explicit_permissions_is_rejected_before_any_app_start(monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_ui.cli as cli_module
    import a13n_ui.terminal as terminal_module
    from click.testing import CliRunner

    def unexpected_start(request):
        raise AssertionError("Conflicting resume must be rejected before starting the App")

    monkeypatch.setattr(terminal_module, "start", unexpected_start)
    result = CliRunner().invoke(cli_module.cli, ["--resume", "session-1", "--environment-mode", "sandbox"])
    assert result.exit_code == 2
    assert "cannot be combined" in result.output


@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["approve", "deny"])
@pytest.mark.parametrize("mode", ["full-control", "sandbox"])
async def test_pending_shell_decision_is_reviewed_and_resumed_through_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision: str, mode: str
) -> None:
    if mode == "sandbox" and os.environ.get("A13N_UI_TEST_SANDBOX") != "1":
        pytest.skip("Set A13N_UI_TEST_SANDBOX=1 with a compatible envd binary and native isolation support")
    import a13n_ui.model_runtime as runtime
    import yaml
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall

    path = await _seed(tmp_path, monkeypatch)
    agent_path = path.parent / "agents/codex.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["capabilities"].append(
        {
            "capability": "ShellReviewCapability",
            "configuration": {
                "model": "model-codex",
                "risk_threshold": "high",
                "on_flagged": "approval_required",
                "on_error": "approval_required",
            },
        }
    )
    agent_path.write_text(yaml.safe_dump(agent))
    marker = tmp_path / "approved-result"

    async def stream(messages, info):
        if not info.function_tools:
            yield '{"risk":"high","reason":"Needs review"}'
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
        "build_codex_model",
        lambda *args, **kwargs: FunctionModel(stream_function=stream, profile={"supports_json_object_output": True}),
    )
    envd = EnvdRuntimeSettings(executable=Path(os.environ["A13N_AGENT_ENVD_EXECUTABLE"]) if mode == "sandbox" else None)
    async with open_agent_ui_app(
        AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), envd_runtime=envd),
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
        assert "Needs review" in interaction.prompt()
        response = interaction.accept("yes" if decision == "approve" else "no")
        assert response is not None and not isinstance(response, str)
        assert await backend.execute(StreamRenderer(backend.status), response=response) == ""
        assert marker.exists() is (decision == "approve"), (
            await app.get_thread_transcript(thread_id=backend.thread_id)
        ).model_dump_json()
        assert await backend.pending() == ""


@pytest.mark.anyio
async def test_native_image_input_reaches_model_and_survives_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from io import BytesIO

    import a13n_ui.model_runtime as runtime
    from PIL import Image
    from pydantic_ai import BinaryContent
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    path = await _seed(tmp_path, monkeypatch)
    stream = BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, format="PNG")
    data = stream.getvalue()
    observed = []

    async def stream_model(messages, info):
        observed.append(messages)
        yield "Image received"

    monkeypatch.setattr(
        runtime,
        "build_codex_model",
        lambda *args, **kwargs: FunctionModel(stream_function=stream_model, profile={"supports_thinking": True}),
    )
    async with open_agent_ui_app(
        AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.initialize()
        await backend.execute(
            StreamRenderer(backend.status), prompt=(BinaryContent(data=data, media_type="image/png"),)
        )
        await backend.execute(StreamRenderer(backend.status), prompt="Recall the image")
        assert len(observed) == 2
        for messages in observed:
            contents = [
                item
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart) and not isinstance(part.content, str)
                for item in part.content
                if isinstance(item, BinaryContent)
            ]
            assert any(item.data == data and item.media_type == "image/png" for item in contents)


@pytest.mark.anyio
async def test_onboarding_import_enrolls_inheriting_subagent_and_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = await _seed(tmp_path, monkeypatch)
    external = tmp_path / ".claude/agents/reviewer.md"
    external.parent.mkdir(parents=True)
    source = (
        "---\nname: reviewer\ndescription: Review code.\nmodel: sonnet\ntools: Read, Bash\n---\nInspect carefully.\n"
    )
    external.write_text(source)
    async with open_agent_ui_app(
        AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.initialize()
        choices, preview = await backend.import_choices("claude-code", "project")
        assert [item.value for item in choices] == ["reviewer"]
        assert "inherit" in preview
        assert "enabled" in await backend.import_and_enroll(("reviewer",))
        configuration = await app.current_configuration()
        assert configuration.subagents["subagent-reviewer"].model is None
        assert configuration.subagents["subagent-reviewer"].tools is None
        assert any(edge.markdown == "subagent-reviewer" for edge in configuration.agents["agent-codex"].subagents)
        await backend.import_choices("claude-code", "project")
        assert "enabled" in await backend.import_and_enroll(("reviewer",))
        configuration = await app.current_configuration()
        assert len(configuration.agents["agent-codex"].subagents) == 1
        assert external.read_text() == source
