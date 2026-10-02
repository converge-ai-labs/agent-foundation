from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.composition import AgentCompositionResolver
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.configuration.setup import SetupSelection, preview_setup, publish_setup
from a13n_harness_ui.extensions import HarnessUiExtensionCatalog
from a13n_harness_ui.interactive.backend import SessionBackend
from a13n_harness_ui.interactive.commands import Command, CommandRegistry
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer, terminal_text
from a13n_harness_ui.interactive.setup import SetupWizard
from a13n_harness_ui.model_authoring import ModelRecipeRequest, prepare_model
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from pydantic_ai.models.function import FunctionModel


async def _retained_history(backend: SessionBackend) -> str:
    from a13n_harness_ui.interactive.history import restore_transcript

    page = await backend.app.get_thread_transcript(thread_id=backend.thread_id, limit=50)
    renderer = StreamRenderer(backend.status)
    try:
        restore_transcript(renderer, page)
        return "\n".join(block.source for block in renderer.transcript.blocks.values())
    finally:
        renderer.transcript.close()


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
    assert registry.parse("/fast ultrafast").arguments == ("ultrafast",)
    assert registry.completions("/fast u")[0][0] == "ultrafast"
    with pytest.raises(ValueError, match="unavailable"):
        registry.parse("/fast ultrafast", busy=True)
    registry.thinking_choices = (("default", "Configured native value"), ("off", "Disable thinking"))
    assert registry.completions("/thinking o") == (("off", "Disable thinking"),)
    assert registry.completions("/thinking h") == ()
    assert "Ctrl+J" in registry.help()
    assert "Alt+Enter" in registry.help()
    for invalid in ("/unknown", "/model a b", "/mode invalid", '/attach "unclosed'):
        with pytest.raises(ValueError):
            registry.parse(invalid)
    with pytest.raises(ValueError, match="unavailable"):
        registry.parse("/thinking high", busy=True)
    with pytest.raises(ValueError, match="Duplicate"):
        CommandRegistry((Command("one", "one", aliases=("two",)), Command("two", "two")))


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize(
    "elapsed, expected",
    [
        (0, "0s"),
        (0.6, "1s"),
        (42, "42s"),
        (59.4, "59s"),
        (59.6, "1m 00s"),
        (60, "1m 00s"),
        (192, "3m 12s"),
        (3599.4, "59m 59s"),
        (3599.6, "1h 00m 00s"),
        (3600, "1h 00m 00s"),
        (3909, "1h 05m 09s"),
        (90_061, "25h 01m 01s"),
    ],
)
def test_status_elapsed_uses_hours_minutes_seconds(
    monkeypatch: pytest.MonkeyPatch, running: bool, elapsed: float, expected: str
) -> None:
    monkeypatch.setattr("a13n_harness_ui.interactive.rendering.time.monotonic", lambda: 1000 + elapsed)
    status = Status(started=1000 if running else None, elapsed=0 if running else elapsed)
    assert status.line().endswith(f" · {expected} ")


@pytest.mark.parametrize("width", [40, 80, 120])
def test_status_elapsed_respects_terminal_width(width: int) -> None:
    from prompt_toolkit.utils import get_cwidth

    status = Status(elapsed=3909)
    full = status.line()
    assert full.endswith(" · 1h 05m 09s ")
    assert status.line(get_cwidth(full)) == full
    assert "1h" not in status.line(get_cwidth(full) - 1)
    assert get_cwidth(status.line(width)) <= width


def test_renderer_modes_switch_without_replay_and_preserve_control_safety() -> None:
    status = Status()
    renderer = StreamRenderer(status)
    renderer.ingest("REASONING_MESSAGE_CONTENT", {"delta": "hidden summary"})
    renderer.ingest("TEXT_MESSAGE_CONTENT", {"delta": "Hello "})
    assert renderer.drain() == "hidden summaryHello "
    assert renderer.drain() == ""
    status.mode = "detailed"
    renderer.ingest("REASONING_MESSAGE_CONTENT", {"delta": "public reasoning"})
    renderer.ingest("TOOL_CALL_START", {"toolCallId": "edit-1", "toolCallName": "edit"})
    renderer.ingest("TOOL_CALL_ARGS", {"toolCallId": "edit-1", "delta": '{"file_path":"a.py"}'})
    renderer.ingest("TOOL_CALL_END", {"toolCallId": "edit-1"})
    renderer.ingest("TOOL_CALL_RESULT", {"toolCallId": "edit-1", "content": "edited"})
    result = renderer.drain()
    assert "public reasoning" in result
    tool = next(block for block in renderer.transcript.blocks.values() if "edit | returned" in block.source)
    assert "edited" in tool.source and "file_path" not in tool.source
    assert "hidden summary" not in result
    assert "\x1b" not in terminal_text("unsafe\x1b]52;c;YQ==\x07")
    renderer.ingest("TEXT_MESSAGE_CONTENT", None)
    assert renderer.gap


def test_stream_argument_and_result_memory_is_bounded() -> None:
    renderer = StreamRenderer(Status(mode="detailed"), limit=128)
    renderer.ingest("TOOL_CALL_START", {"toolCallId": "one", "toolCallName": "write"})
    for _ in range(1000):
        renderer.ingest("TOOL_CALL_ARGS", {"toolCallId": "one", "delta": "x" * 1000})
    renderer.ingest("TOOL_CALL_END", {"toolCallId": "one"})
    renderer.ingest("TOOL_CALL_RESULT", {"content": "\n".join(["line"] * 100)})
    assert len(renderer.drain()) < 2048
    assert "Arguments exceed display budget" in "".join(block.source for block in renderer.transcript.blocks.values())


def test_tool_streams_are_correlated_bounded_and_do_not_override_root_cancellation() -> None:
    status = Status(mode="detailed")
    renderer = StreamRenderer(status, limit=128)
    for run_id in ("child-a", "child-b"):
        renderer.ingest("TOOL_CALL_START", {"toolCallId": "one", "toolCallName": "edit"}, child=True, run_id=run_id)
        renderer.ingest("TOOL_CALL_END", {"toolCallId": "one"}, child=True, run_id=run_id)
    for run_id in ("child-b", "child-a"):
        renderer.ingest("TOOL_CALL_RESULT", {"toolCallId": "one", "content": "done"}, child=True, run_id=run_id)
    result = renderer.drain()
    assert "edit | returned" in result and "child-a" in result
    assert "child-b" in result
    for index in range(256):
        renderer.ingest("TOOL_CALL_START", {"toolCallId": str(index), "toolCallName": "edit"})
    assert len(renderer._tools) == 128
    renderer.ingest("TEXT_MESSAGE_CONTENT", {"delta": "answer"})
    assert status.state == "responding"
    status.state = "cancelling"
    renderer.ingest("TOOL_CALL_START", {"toolCallId": "late", "toolCallName": "edit"})
    renderer.ingest("TEXT_MESSAGE_CONTENT", {"delta": "late answer"})
    assert status.state == "cancelling"


def test_setup_choices_expand_to_explicit_native_context_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a13n_harness_ui.interactive.setup.local_sandbox_supported", lambda: True)
    wizard = SetupWizard(advanced=True)
    wizard.accept("codex")
    wizard.accept("gpt-6-sol")
    for value in ("on", "all", "extended", "medium", "", "no", "", "sandbox"):
        wizard.accept(value)
    assert wizard.question is None
    selection = wizard.selection("/tmp")
    assert selection["model"]["route"] == "openai-codex:gpt-6-sol"
    characteristics = selection["model"]["model_characteristics"]
    assert characteristics["context_window_tokens"] == 872000
    assert characteristics["proactive_context_management_threshold"] == 0.65
    assert characteristics["compact_threshold"] == 0.9
    assert selection["environment_profile"] == "environment-sandbox"
    assert selection["model"]["settings"]["thinking"] == "medium"


def test_cli_help_never_imports_runtime(tmp_path: Path) -> None:
    invocations = [
        ["--help"],
        ["--version"],
        ["login", "--help"],
        ["run", "--help"],
        ["add", "--help"],
        ["add", "agent", "--help"],
        ["add", "model", "--help"],
    ]
    # One interpreter checks every help path: a runtime import after any call fails at that call.
    script = f"""
import sys
from a13n_harness_ui.cli import main
for arguments in {invocations!r}:
    try:
        main(arguments)
    except SystemExit as exit:
        assert exit.code in (None, 0), (arguments, exit.code)
    for prefix in ('a13n_harness', 'pydantic', 'sqlalchemy', 'textual', 'fastapi', 'prompt_toolkit', 'a13n_harness_ui.app'):
        assert not any(name == prefix or name.startswith(prefix + '.') for name in sys.modules), (arguments, prefix)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / ".a13n-harness-ui").exists()


async def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    (tmp_path / "codex").mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = tmp_path / "config" / "a13n-harness-ui.yaml"
    selection = SetupSelection(
        model=prepare_model(
            ModelRecipeRequest(connection="codex", model_id="gpt-5.6-sol", settings={"thinking": "high"})
        ),
        default_agent="agent-codex",
        project="project-local",
        project_path=str(tmp_path),
        environment_profile="environment-native",
        shell_review=False,
    )
    validate = AgentCompositionResolver(HarnessUiExtensionCatalog()).validate_generation
    await preview_setup(path, selection, validate_candidate=validate)
    result = await publish_setup(path, selection, validate_candidate=validate)
    assert result.completed
    return path


@pytest.mark.anyio
async def test_global_and_exact_cwd_guidance_reach_the_first_model_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_harness.models.codex as runtime

    path = await _seed(tmp_path, monkeypatch)
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    (tmp_path / "AGENTS.md").write_text("DO NOT INJECT ANCESTOR")
    (path.parent / "AGENTS.md").write_text("GLOBAL GUIDANCE")
    (path.parent / "AGENTS.override.md").write_text("DO NOT INJECT OVERRIDE")
    (path.parent / "RULES.md").write_text("DO NOT INJECT RULES")
    (tmp_path / "codex" / "AGENTS.md").write_text("DO NOT INJECT CODEX HOME")
    (cwd / "AGENTS.md").write_text(
        "\n".join(f"Repository rule {index}" for index in range(150)) + "\nFINAL REPOSITORY RULE"
    )
    seen = []

    async def stream(messages, info):
        seen.append((messages, info.instructions))
        yield "done"

    monkeypatch.setattr(runtime, "CodexRequestModel", lambda *args, **kwargs: FunctionModel(stream_function=stream))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), cwd, Status())
        assert await backend.initialize()
        renderer = StreamRenderer(backend.status)
        assert await backend.execute(renderer, prompt="Do the task") == ""
        display = renderer.drain()
        assert "GLOBAL GUIDANCE" not in display
        assert "surface-context" not in display
        assert "Repository rule" not in display
        sources = [block.source for block in renderer.transcript.blocks.values()]
        assert sum(source == "> Do the task" for source in sources) == 1, sources
        page = await app.get_thread_transcript(thread_id=backend.thread_id, limit=50)
        hidden = [part for entry in page.entries for part in entry.parts if not part.metadata.display]
        assert any("GLOBAL GUIDANCE" in (part.text or "") for part in hidden)
        assert any("Harness UI TUI" in (part.text or "") for part in hidden)
        assert any("FINAL REPOSITORY RULE" in (part.text or "") for part in hidden)
        # A fresh adapter reads retained native metadata, not transient renderer state.
        resumed = SessionBackend(app, CliRequest(), cwd, Status())
        await resumed.resume(backend.thread_id)
        assert "GLOBAL GUIDANCE" not in await _retained_history(resumed)
        history = await _retained_history(backend)
        assert "Do the task" in history and "done" in history
        assert "surface-context" not in history
        assert "GLOBAL GUIDANCE" not in history
        assert "Repository rule" not in history
        (path.parent / "AGENTS.md").unlink()
        await app.reload_configuration()
        assert await backend.execute(StreamRenderer(backend.status), prompt="Continue") == ""
    assert len(seen) == 2
    assert "No global AGENTS.md instructions are configured" in str(seen[1][0])
    assert "GLOBAL GUIDANCE" not in (seen[0][1] or "")
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    user_text = "\n".join(
        str(part.content)
        for message in seen[0][0]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    )
    assert "GLOBAL GUIDANCE" in user_text
    assert '<surface-context source="a13n-harness-ui">' in user_text
    assert "Harness UI TUI" in user_text
    visible = str(seen[0])
    assert "Repository rule 0" in visible and "FINAL REPOSITORY RULE" in visible
    assert "DO NOT INJECT" not in visible
    assert "File context truncated" not in visible


@pytest.mark.anyio
async def test_session_overrides_capture_native_context_and_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_harness.models.codex as runtime

    path = await _seed(tmp_path, monkeypatch)
    before = (path.parent / "models/codex.yaml").read_bytes()
    efforts = []

    async def stream(messages, info):
        efforts.append(info.model_request_parameters.thinking)
        yield "Hello "
        yield "from the mock."

    monkeypatch.setattr(
        runtime,
        "CodexRequestModel",
        lambda *args, **kwargs: FunctionModel(stream_function=stream, profile={"supports_thinking": True}),
    )
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        status = Status()
        backend = SessionBackend(app, CliRequest(), tmp_path, status)
        assert await backend.initialize()
        assert status.context_window == 350000
        choices = await backend.choices("thinking")
        assert {item.value for item in choices} == {"default", "off", "low", "medium", "high", "xhigh"}
        await backend.thinking("low")
        assert status.thinking == "Low"
        assert "Thinking Low" in status.line(160)
        with pytest.raises(ValueError, match="Unsupported"):
            await backend.thinking("minimal")
        assert backend.overrides.thinking == "low"
        renderer = StreamRenderer(status)
        assert await backend.execute(renderer, prompt="First") == ""
        assert "Hello from the mock." in renderer.drain()
        thread_id = backend.thread_id
        thread_usage = await app.thread_usage(thread_id=thread_id)
        assert thread_usage.root.model_requests == 1
        assert thread_usage.combined.model_requests == 1
        assert thread_usage.recent_runs[0].totals.model_requests == 1
        usage = await app.context_usage(thread_id)
        assert usage.thinking == "low"
        assert usage.context_window == 350000
        assert usage.latest_request_tokens is not None
        await backend.thinking("medium")
        assert await backend.execute(StreamRenderer(status), prompt="Second") == ""
        assert (await app.context_usage(thread_id)).thinking == "medium"
        assert (await app.thread_usage(thread_id=thread_id)).combined.model_requests == 2
        resumed = SessionBackend(app, CliRequest(thread_id=thread_id), tmp_path, Status())
        from unittest.mock import AsyncMock

        from a13n_harness_ui.interactive.shell import CliShell
        from prompt_toolkit.application import create_app_session
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        original_get = app.get_thread
        spy = AsyncMock(wraps=original_get)
        monkeypatch.setattr(app, "get_thread", spy)
        await resumed.initialize()
        spy.assert_awaited_once_with(thread_id)
        assert resumed.resumed_transcript is not None
        with create_pipe_input(), create_app_session(output=DummyOutput()):
            shell = CliShell(CliRequest(thread_id=thread_id), status=resumed.status)
            shell.backend = resumed
            shell._restore_resumed_history()
            displayed = "\n".join(block.source for block in shell.renderer.transcript.blocks.values())
            assert "First" in displayed and "Second" in displayed and "Hello from the mock." in displayed
            shell.emit("temporary screen content")
            await shell._resume(thread_id)
            displayed = "\n".join(block.source for block in shell.renderer.transcript.blocks.values())
            assert "First" in displayed and "temporary screen content" not in displayed
            shell.renderer.transcript.close()
        assert resumed.overrides.thinking == "medium"
        assert "First" in await _retained_history(resumed)
        assert (await app.get_thread(thread_id)).thread.configuration.version == 1
        from a13n_harness_ui.cli_runtime import _run_one_shot

        monkeypatch.chdir(tmp_path)
        assert await _run_one_shot(app, CliRequest(command="run", thread_id=thread_id, prompt="One-shot resume")) == 0
    assert efforts == ["low", "medium", "medium"]
    assert (path.parent / "models/codex.yaml").read_bytes() == before
    config = await load_harness_ui_configuration(path)
    assert config.models["model-codex"].settings["thinking"] == "high"


@pytest.mark.anyio
async def test_terminal_flush_retains_semantic_output_independently_of_draw() -> None:
    from a13n_harness_ui.interactive.shell import CliShell
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
    import a13n_harness.models.codex as runtime
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
        "CodexRequestModel",
        lambda *args, **kwargs: FunctionModel(function=reply, stream_function=stream_reply),
    )
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        first = await backend.execute(StreamRenderer(backend.status), prompt="First request")
        assert first == ""
        second_renderer = StreamRenderer(backend.status)
        second = await backend.execute(second_renderer, prompt="Continue")
        assert second == ""
        assert len(calls) == 3, "Two foreground requests plus one same-agent compaction request"
        history = await _retained_history(backend)
        assert "Mock context summary" in history


def test_context_samples_replace_root_requests_without_double_counting_cache_or_children() -> None:
    from datetime import UTC, datetime

    from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord
    from a13n_harness_ui.live import LiveEvent, model_usage, root_context_samples

    root = ModelUsageRecord(
        call_id="call_fixture",
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
    auxiliary = root.model_copy(update={"record_id": "aux-1", "source": "files.media_understanding"})
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
                    "schema_version": "1",
                    "payload": {
                        "type": "usage_report",
                        "records": [item.model_dump(mode="json") for item in (root, child, delegated, auxiliary)],
                    },
                }
            }
        },
    )
    assert len(model_usage(event)) == 4
    samples = root_context_samples(event)
    assert len(samples) == 1
    assert samples[0].tokens == 120
    assert samples[0].response_ordinal == 2
    assert root_context_samples(event.model_copy(update={"run_kind": "child"})) == ()
    resumed = event.model_copy(deep=True, update={"run_id": "run-resumed"})
    resumed.payload["value"]["run_id"] = "run-resumed"
    resumed.payload["value"]["event"]["payload"]["usage_id"] = "scope-original"
    (sample,) = root_context_samples(resumed)
    assert sample.run_id == "run-resumed" and sample.tokens == 120
    resumed.payload["value"]["run_id"] = "inline-child-run"
    assert root_context_samples(resumed) == ()


def test_resume_with_explicit_permissions_is_rejected_before_any_app_start(monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_harness_ui.cli as cli_module
    import a13n_harness_ui.terminal as terminal_module
    from click.testing import CliRunner

    def unexpected_start(request):
        raise AssertionError("Conflicting resume must be rejected before starting the App")

    monkeypatch.setattr(terminal_module, "start", unexpected_start)
    result = CliRunner().invoke(cli_module.cli, ["--resume", "session-1", "--environment-mode", "full-control"])
    assert result.exit_code == 2
    assert "cannot be combined" in result.output


@pytest.mark.anyio
@pytest.mark.parametrize("disable_media", [False, True])
async def test_setup_model_view_uses_declared_media_without_an_external_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disable_media: bool
) -> None:
    from io import BytesIO

    import a13n_harness.models.codex as runtime
    import yaml
    from PIL import Image
    from pydantic_ai import BinaryContent
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall

    path = await _seed(tmp_path, monkeypatch)
    model_path = path.parent / "models/codex.yaml"
    document = yaml.safe_load(model_path.read_text(encoding="utf-8"))
    assert document["model_characteristics"]["capabilities"] == ["image_understanding"]
    if disable_media:
        document["model_characteristics"]["capabilities"] = []
        model_path.write_text(yaml.safe_dump(document), encoding="utf-8")
    stream = BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, format="PNG")
    data = stream.getvalue()
    (tmp_path / "image.png").write_bytes(data)
    observed = []
    returns = []
    monkeypatch.delenv("A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL", raising=False)

    async def stream_model(messages, info):
        observed.extend(messages)
        returns.extend(
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        )
        if returns:
            yield "Image handled"
        else:
            yield {0: DeltaToolCall(name="view", tool_call_id="view-image", json_args='{"file_path":"image.png"}')}

    monkeypatch.setattr(
        runtime, "CodexRequestModel", lambda *args, **kwargs: FunctionModel(stream_function=stream_model)
    )
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.execute(StreamRenderer(backend.status), prompt="View image.png")
    contents = [
        item
        for message in observed
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and isinstance(part.content, list)
        for item in part.content
        if isinstance(item, BinaryContent)
    ]
    if disable_media:
        assert not contents
        assert any("media_understanding_unavailable" in str(part.content) for part in returns)
    else:
        assert any(item.data == data and item.media_type == "image/png" for item in contents)


@pytest.mark.anyio
async def test_native_image_input_reaches_model_and_survives_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from io import BytesIO

    import a13n_harness.models.codex as runtime
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
        "CodexRequestModel",
        lambda *args, **kwargs: FunctionModel(stream_function=stream_model, profile={"supports_thinking": True}),
    )
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
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
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.initialize()
        choices, preview = await backend.import_choices("claude-code", "project")
        assert [item.value for item in choices] == ["reviewer"]
        assert "inherit" in preview
        assert "enabled" in await backend.import_and_enroll(("reviewer",))
        configuration = await app.current_configuration()
        assert configuration.subagents["subagent-reviewer"].tools is None
        assert any(edge.markdown == "subagent-reviewer" for edge in configuration.agents["agent-codex"].subagents)
        await backend.import_choices("claude-code", "project")
        assert "enabled" in await backend.import_and_enroll(("reviewer",))
        configuration = await app.current_configuration()
        assert len(configuration.agents["agent-codex"].subagents) == 1
        assert external.read_text() == source


@pytest.mark.anyio
async def test_active_guidance_reaches_native_model_in_order_without_another_root_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    import a13n_harness.models.codex as runtime
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    path = await _seed(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    observed = []

    async def stream_model(messages, info):
        observed.append(messages.copy())
        if len(observed) == 1:
            entered.set()
            yield "Working"
            await release.wait()
        else:
            yield "Guidance received"

    monkeypatch.setattr(
        runtime, "CodexRequestModel", lambda *args, **kwargs: FunctionModel(stream_function=stream_model)
    )
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        renderer = StreamRenderer(backend.status)
        task = asyncio.create_task(backend.execute(renderer, prompt="Start work"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            receipt = backend.receipt_id
            assert receipt is not None
            assert "sent" in await backend.steer("guidance-first", receipt_id=receipt)
            assert "sent" in await backend.steer("guidance-second", receipt_id=receipt)
            release.set()
            assert await asyncio.wait_for(task, 10) == ""
            assert len(observed) == 2
            contents = [
                part.content
                for message in observed[-1]
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
            ]
            assert contents.index("guidance-first") < contents.index("guidance-second")
            assert backend.receipt_id is None
            with pytest.raises(ValueError, match="no longer running"):
                await backend.steer("too late", receipt_id=receipt)
        finally:
            release.set()
            if not task.done():
                await backend.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("count", [1, 2])
async def test_enqueued_bodies_render_once_with_delivery_notices(count: int) -> None:
    from a13n_harness import AgentContext, AgentSpec, HarnessBuilder, HarnessEvent, RunBindings
    from a13n_harness.events import InputTextEvent
    from a13n_stream_protocol import HarnessAguiObserver
    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.messages import EnqueuedMessagesEvent

    queued = [f"ENQUEUE_BODY_{index}" for index in range(count)]

    class EnqueueOnce(AbstractCapability[AgentContext]):
        id = "test.enqueue-once"

        def __init__(self):
            self.sent = False

        async def before_model_request(self, ctx, request_context):
            if not self.sent:
                self.sent = True
                for text in queued:
                    ctx.enqueue(text)
            return request_context

    async def respond(messages, info):
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond), capabilities=(EnqueueOnce(),)
    )
    observer = HarnessAguiObserver()
    renderer = StreamRenderer(Status())
    batches, deliveries = [], []
    try:
        async with executable.stream("INITIAL_BODY", bindings=RunBindings.embedded()) as stream:
            async for source in stream:
                if isinstance(source, HarnessEvent):
                    if isinstance(source.event, InputTextEvent) and source.event.source == "user":
                        batches.append([source.event.content])
                    elif isinstance(source.event, EnqueuedMessagesEvent):
                        deliveries.append(source.event.enqueue_id)
                for event in observer.observe(source):
                    renderer.ingest(event.type.value, event.model_dump(mode="json"))
        rendered = "\n".join(block.source for block in renderer.transcript.blocks.values())
        assert [text for batch in batches for text in batch] == ["INITIAL_BODY"]
        assert len(set(deliveries)) == count
        for text in ["INITIAL_BODY", *queued]:
            assert rendered.count(text) == 1
        for enqueue_id in deliveries:
            assert enqueue_id not in rendered
    finally:
        renderer.transcript.close()


@pytest.mark.anyio
async def test_codeact_values_survive_ui_continuation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_harness.models.codex as runtime
    import yaml
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall

    path = await _seed(tmp_path, monkeypatch)
    root = yaml.safe_load(path.read_text())
    root["tools"]["enable_ask_user_question"] = False
    path.write_text(yaml.safe_dump(root))
    requests = 0

    async def stream(messages, info):
        nonlocal requests
        names = {tool.name for tool in info.function_tools}
        assert {"run_code", "run_program", "store", "load", "forget"} <= names
        assert "ask_user_question" not in names
        step = requests
        requests += 1
        if step in (0, 2):
            code = 'await store(key="sum", value=2)\nawait load(key="sum")' if step == 0 else 'await load(key="sum")'
            yield {0: DeltaToolCall(name="run_code", tool_call_id=f"code-{step}", json_args=json.dumps({"code": code}))}
        else:
            returned = [
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_name == "run_code"
            ]
            assert returned[-1].tool_call_id == f"code-{step - 1}"
            assert returned[-1].content == 2
            yield "Stored value is available."

    monkeypatch.setattr(runtime, "CodexRequestModel", lambda *args, **kwargs: FunctionModel(stream_function=stream))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        first = SessionBackend(app, CliRequest(), tmp_path, Status())
        renderer = StreamRenderer(first.status)
        try:
            assert await first.execute(renderer, prompt="Store the value") == ""
            assert "Stored value is available" in "".join(block.source for block in renderer.transcript.blocks.values())
            resumed = SessionBackend(app, CliRequest(thread_id=first.thread_id), tmp_path, Status())
            await resumed.initialize()
            assert await resumed.execute(renderer, prompt="Load the saved value") == ""
        finally:
            renderer.transcript.close()
    assert requests == 4


@pytest.mark.anyio
async def test_agent_switch_changes_full_recipe_keeps_history_and_survives_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_harness.models.codex as runtime
    from a13n_harness_ui.interactive.commands import CommandRegistry

    path = await _seed(tmp_path, monkeypatch)
    second = SetupSelection(
        model=prepare_model(ModelRecipeRequest(connection="codex", model_id="gpt-6-astra")),
        new_agent_id="agent-astra",
        new_agent_name="Astra",
        instructions="SECOND AGENT INSTRUCTIONS",
        project="project-local",
        project_path=str(tmp_path),
        environment_profile="environment-native",
        shell_review=False,
    )
    seen = []

    async def stream(messages, info):
        seen.append(info.instructions)
        yield "Done"

    monkeypatch.setattr(
        runtime,
        "CodexRequestModel",
        lambda *args, **kwargs: FunctionModel(stream_function=stream, profile={"supports_thinking": True}),
    )
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        await app.preview_setup(second)
        assert (await app.apply_setup(second)).completed
        baseline = {p: p.read_bytes() for p in path.parent.rglob("*.yaml")}
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.initialize()
        await backend.thinking("low")
        await backend.execute(StreamRenderer(backend.status), prompt="First agent")
        thread_id = backend.thread_id
        assert CommandRegistry().parse("/agent agent-astra").command.name == "agent"
        with pytest.raises(ValueError, match="unavailable"):
            CommandRegistry().parse("/agent agent-astra", busy=True)
        await backend.agents("agent-astra")
        assert backend.thread_id == thread_id
        assert backend.overrides.thinking is None
        assert backend.status.model == "openai-codex:gpt-6-astra"
        assert "First agent" in await _retained_history(backend)
        resumed = SessionBackend(app, CliRequest(thread_id=thread_id), tmp_path, Status())
        await resumed.initialize()
        assert resumed.status.model == "openai-codex:gpt-6-astra"
        assert resumed.overrides.thinking is None
        await resumed.execute(StreamRenderer(resumed.status), prompt="Second agent")
        assert "SECOND AGENT INSTRUCTIONS" in seen[-1]
        assert "SECOND AGENT INSTRUCTIONS" not in seen[0]
        assert all(p.read_bytes() == content for p, content in baseline.items())
        assert (await app.get_thread(thread_id)).thread.configuration.agent_source.id == "agent-astra"
        assert all("review" not in choice.value for choice in await backend.choices("agent"))
        await resumed.new()
        fresh = await resumed.ensure_session()
        assert fresh != thread_id
        assert (await app.get_thread(fresh)).thread.configuration.agent_source.id == "agent-astra"


@pytest.mark.anyio
async def test_usage_updates_before_root_operation_completes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    import a13n_harness.models.codex as runtime
    from pydantic_ai.models.function import DeltaToolCall

    path = await _seed(tmp_path, monkeypatch)
    release, second_request, updated = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {0: DeltaToolCall(name="shell_exec", json_args='{"command":"echo ready"}', tool_call_id="call-live")}
        else:
            second_request.set()
            await release.wait()
            yield "Done"

    monkeypatch.setattr(runtime, "CodexRequestModel", lambda *args, **kwargs: FunctionModel(stream_function=stream))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        status = Status(state="working")
        backend = SessionBackend(app, CliRequest(), tmp_path, status)

        async def flush():
            if status.requests:
                updated.set()

        task = asyncio.create_task(backend.execute(StreamRenderer(status), prompt="Run a command", flush=flush))
        try:
            await asyncio.wait_for(second_request.wait(), 10)
            await asyncio.wait_for(updated.wait(), 10)
            assert not task.done()
            assert status.requests == 1
            assert (await app.thread_usage(thread_id=backend.thread_id)).combined.model_requests == 1
            assert status.context_tokens is not None
            assert "ctx " in status.line(80)
            assert "cost --" in status.line(80)  # FunctionModel has no invented price.
            assert "in " not in status.line(80) and "cache 0.0%" in status.line(80)
        finally:
            release.set()
            await asyncio.wait_for(task, 10)
        assert status.requests == 2
        first_thread = backend.thread_id
        release.clear()
        second_request.clear()
        task = asyncio.create_task(backend.execute(StreamRenderer(status), prompt="Continue", flush=flush))
        try:
            await asyncio.wait_for(second_request.wait(), 10)
            assert not task.done()
            assert status.requests == 2  # The next Run does not clear the Thread baseline.
        finally:
            release.set()
            await asyncio.wait_for(task, 10)
        assert status.requests == 3
        totals = (await app.thread_usage(thread_id=first_thread)).root
        assert totals.model_requests == 3  # Live + terminal reports count once.
        assert status.usage.input_tokens == dict(totals.tokens)["input_tokens"]
        assert status.total_tokens == dict(totals.tokens)["input_tokens"] + dict(totals.tokens)["output_tokens"]
        latest_context = status.context_tokens
        assert latest_context == (await app.context_usage(first_thread)).latest_request_tokens
        assert latest_context < status.usage.input_tokens + status.usage.output_tokens

    # A new App/terminal restores durable accounting, not just in-process state.
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        status = Status()
        backend = SessionBackend(app, CliRequest(thread_id=first_thread), tmp_path, status)
        await backend.initialize()
        assert status.requests == 3
        assert status.usage.input_tokens == dict(totals.tokens)["input_tokens"]
        assert status.total_tokens == dict(totals.tokens)["input_tokens"] + dict(totals.tokens)["output_tokens"]
        assert status.usage.cost == totals.model_cost_usd
        assert status.unknown_costs == totals.unknown_model_costs
        assert "cost --" in status.line()
        assert status.context_tokens == latest_context
        await backend.new()
        assert status.requests == 0 and status.usage is None and status.context_tokens is None
        await backend.execute(StreamRenderer(status), prompt="Separate Thread")
        other_thread = backend.thread_id
        assert other_thread != first_thread and status.requests == 1
        await backend.resume(first_thread)
        assert status.requests == 3
        assert status.context_tokens == latest_context
        await backend.resume(other_thread)
        assert status.requests == 1


def test_add_agent_cli_routes_to_terminal_with_explicit_advanced_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_harness_ui.cli as cli_module
    import a13n_harness_ui.terminal as terminal_module
    from click.testing import CliRunner

    seen = []
    monkeypatch.setattr(terminal_module, "start", seen.append)
    result = CliRunner().invoke(cli_module.cli, ["--no-update-check", "add", "agent", "--advanced"])
    assert result.exit_code == 0, result.output
    assert len(seen) == 1
    assert seen[0].command == "add" and seen[0].action == "agent"
    assert seen[0].setup_advanced and seen[0].no_update_check


@pytest.mark.anyio
async def test_default_notes_are_injected_and_survive_resume_with_full_projection(tmp_path: Path, monkeypatch) -> None:
    import a13n_harness.models.codex as runtime
    from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
    from pydantic_ai.models.function import DeltaToolCall

    path = await _seed(tmp_path, monkeypatch)
    value = "Long detailed note. " * 30 + "FINAL NOTE DETAIL"
    injected = False

    async def stream(messages, info):
        nonlocal injected
        assert {"note_write", "note_delete", "note_get"} <= {tool.name for tool in info.function_tools}
        results = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "note_write"
        ]
        if not results:
            yield {
                0: DeltaToolCall(
                    name="note_write", tool_call_id="note-one", json_args=json.dumps({"key": "design", "value": value})
                )
            }
        else:
            injected = any(
                value in str(part.content)
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
            )
            yield "Note saved."

    monkeypatch.setattr(runtime, "CodexRequestModel", lambda *args, **kwargs: FunctionModel(stream_function=stream))
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        renderer = StreamRenderer(backend.status)
        await backend.execute(renderer, prompt="Remember this note")
        page = await app.thread_notes(thread_id=backend.thread_id)
        assert page.total == 1 and page.omitted == 0 and page.notes[0].value == value
        renderer.restore_notes(page)
        assert "FINAL NOTE DETAIL" in "\n".join(block.source for block in renderer.transcript.blocks.values())
        thread_id = backend.thread_id
        renderer.transcript.close()
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        assert (await app.thread_notes(thread_id=thread_id)).notes == page.notes
        resumed = SessionBackend(app, CliRequest(thread_id=thread_id), tmp_path, Status())
        await resumed.execute(StreamRenderer(resumed.status), prompt="Use the saved note")
        assert injected
        from a13n_harness_ui.errors import ThreadError

        with pytest.raises(ThreadError, match="continuation changed"):
            await app.thread_notes(thread_id=thread_id, expected_continuation_id="f" * 64)


@pytest.mark.anyio
@pytest.mark.parametrize("tier_key", ["service_tier", "openai_service_tier"])
async def test_fast_override_reaches_model_without_mutating_config_or_reasoning(
    tmp_path: Path, monkeypatch, tier_key: str
) -> None:
    import a13n_harness.models.codex as runtime
    import yaml

    path = await _seed(tmp_path, monkeypatch)
    model_path = path.parent / "models/codex.yaml"
    document = yaml.safe_load(model_path.read_text())
    document["settings"]["service_tier"] = "default"
    document["settings"][tier_key] = "priority"
    model_path.write_text(yaml.safe_dump(document))
    original = {item: item.read_bytes() for item in path.parent.rglob("*.yaml")}
    observed = []

    async def stream(messages, info):
        observed.append(info.model_settings.get("openai_service_tier") or info.model_settings.get("service_tier"))
        yield "Reply."

    monkeypatch.setattr(runtime, "CodexRequestModel", lambda *a, **kw: FunctionModel(stream_function=stream))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.initialize()
        assert backend.status.service_tier == "priority"
        await backend.thinking("low")
        await backend.fast("off")
        assert backend.overrides.thinking == "low"
        assert backend.status.service_tier == "default"
        renderer = StreamRenderer(backend.status)
        try:
            await backend.execute(renderer, prompt="Standard please")
            thread_id = backend.thread_id
            version = (await app.get_thread(thread_id)).thread.configuration.version
            assert "Fast · On" in await backend.fast(None)
            await backend.thinking("medium")
            assert backend.overrides.thinking == "medium"
            assert backend.overrides.fast is True
            await backend.execute(renderer, prompt="Priority please")
            await backend.fast("reset")
            assert backend.overrides.fast is None
            assert backend.status.service_tier == "priority"  # Reset is not an off switch.
            await backend.fast("off")
            await backend.resume(thread_id)
            assert backend.overrides.fast is False
            assert backend.status.service_tier == "default"
            assert (await app.get_thread(thread_id)).thread.configuration.version == version
            restarted = SessionBackend(app, CliRequest(thread_id=thread_id), tmp_path, Status())
            await restarted.initialize()
            assert restarted.overrides.service_tier is None
            assert restarted.status.service_tier == "priority"
            before = backend.overrides
            with pytest.raises(ValueError, match="Usage"):
                await backend.fast("invalid")
            assert backend.overrides == before
            await backend.new()
            assert backend.overrides.fast is False
            await backend.models("default")
            assert backend.overrides.fast is None
            assert backend.status.service_tier == "priority"
        finally:
            renderer.transcript.close()
    assert observed == ["default", "priority"]
    assert all(item.read_bytes() == contents for item, contents in original.items())


@pytest.mark.anyio
async def test_pro_uses_model_default_and_preserves_independent_controls(tmp_path, monkeypatch):
    import a13n_harness.models.codex as runtime
    import yaml

    path = await _seed(tmp_path, monkeypatch)
    model_path = path.parent / "models/codex.yaml"
    document = yaml.safe_load(model_path.read_text())
    document["route"] = "openai-codex:gpt-5.6-sol"
    document["settings"]["openai_reasoning_mode"] = "pro"
    document["settings"]["openai_reasoning_summary"] = "detailed"
    model_path.write_text(yaml.safe_dump(document))
    original = model_path.read_bytes()
    observed = []

    async def stream(messages, info):
        observed.append(dict(info.model_settings))
        yield "Reply."

    monkeypatch.setattr(runtime, "CodexRequestModel", lambda *a, **kw: FunctionModel(stream_function=stream))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.initialize()
        assert backend.status.reasoning_mode == "pro"
        assert backend.status.reasoning_mode_description == "Pro (Model default)"
        await backend.thinking("low")
        await backend.fast("off")
        assert "Standard (session override; Model default: Pro)" in await backend.pro(None)
        assert backend.overrides.reasoning_mode == "standard"
        assert backend.overrides.fast is False
        assert backend.overrides.thinking == "low"
        renderer = StreamRenderer(backend.status)
        try:
            await backend.execute(renderer, prompt="Standard mode")
            thread_id = backend.thread_id
            await backend.pro("reset")
            assert backend.overrides.reasoning_mode is None
            assert backend.status.reasoning_mode == "pro"  # Reset is not Standard.
            await backend.execute(renderer, prompt="Model default mode")
            await backend.pro("off")
            await backend.resume(thread_id)
            assert backend.overrides.reasoning_mode == "standard"
            restarted = SessionBackend(app, CliRequest(thread_id=thread_id), tmp_path, Status())
            await restarted.initialize()
            assert restarted.overrides.reasoning_mode is None
            assert restarted.status.reasoning_mode == "pro"
            await backend.new()
            assert backend.overrides.reasoning_mode == "standard"
            await backend.models("default")
            assert backend.overrides.controls().model_dump(exclude_none=True) == {}
        finally:
            renderer.transcript.close()
    assert [item["openai_reasoning_mode"] for item in observed] == ["standard", "pro"]
    assert all(item["openai_reasoning_summary"] == "detailed" for item in observed)
    assert model_path.read_bytes() == original


@pytest.mark.anyio
async def test_ultrafast_session_switches_and_reset_reach_runtime_without_persisting(tmp_path, monkeypatch):
    import a13n_harness.models.codex as runtime
    import yaml

    path = await _seed(tmp_path, monkeypatch)
    model_path = path.parent / "models/codex.yaml"
    document = yaml.safe_load(model_path.read_text())
    document["route"] = "openai-codex:gpt-6-astra"
    document["settings"]["openai_service_tier"] = "ultrafast"
    model_path.write_text(yaml.safe_dump(document))
    original = model_path.read_bytes()
    observed = []

    async def stream(messages, info):
        observed.append(info.model_settings.get("openai_service_tier") or info.model_settings.get("service_tier"))
        yield "Reply."

    monkeypatch.setattr(runtime, "CodexRequestModel", lambda *a, **kw: FunctionModel(stream_function=stream))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.initialize()
        assert backend.status.fast == "ultrafast"
        assert "Ultrafast" in backend.status.line()
        assert "Ultrafast" in backend.status.line(50)
        renderer = StreamRenderer(backend.status)
        try:
            await backend.thinking("low")
            for command, tier in (
                ("on", "priority"),
                ("ultrafast", "ultrafast"),
                ("off", "default"),
                ("reset", "ultrafast"),
            ):
                await backend.fast(command)
                assert backend.overrides.thinking == "low"
                assert backend.status.service_tier == tier
                await backend.execute(renderer, prompt=f"Use {command}")
            assert backend.overrides.fast is None
            await backend.fast("ultrafast")
            assert backend.overrides.fast == "ultrafast"
            # A different model invalidates only the requested Ultrafast capability.
            document["route"] = "openai-codex:gpt-6-sol"
            document["settings"]["openai_service_tier"] = "priority"
            model_path.write_text(yaml.safe_dump(document))
            await app.reload_configuration()
            before = backend.overrides
            with pytest.raises(ValueError, match="Ultrafast requires"):
                await backend.fast("ultrafast")
            assert backend.overrides == before
            await backend.fast("reset")
            model_path.write_bytes(original)
        finally:
            renderer.transcript.close()
    assert observed == ["priority", "ultrafast", "default", "ultrafast"]
    assert model_path.read_bytes() == original
