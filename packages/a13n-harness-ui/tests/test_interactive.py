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
from a13n_harness_ui.settings import EnvdRuntimeSettings, HarnessUiSettings, StorageSettings
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
    registry.thinking_choices = (("default", "Configured native value"), ("off", "Disable thinking"))
    assert registry.completions("/thinking o") == (("off", "Disable thinking"),)
    assert registry.completions("/thinking h") == ()
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
    renderer.ingest("THINKING_TEXT_MESSAGE_CONTENT", {"delta": "hidden summary"})
    renderer.ingest("TEXT_MESSAGE_CONTENT", {"delta": "Hello "})
    assert renderer.drain() == "hidden summaryHello "
    assert renderer.drain() == ""
    status.mode = "detailed"
    renderer.ingest("REASONING_MESSAGE_CONTENT", {"delta": "public reasoning"})
    renderer.ingest("TOOL_CALL_START", {"tool_call_id": "edit-1", "tool_call_name": "edit"})
    renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "edit-1", "delta": '{"file_path":"a.py"}'})
    renderer.ingest("TOOL_CALL_END", {"tool_call_id": "edit-1"})
    renderer.ingest("TOOL_CALL_RESULT", {"tool_call_id": "edit-1", "content": "edited"})
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
    renderer.ingest("TOOL_CALL_START", {"tool_call_id": "one", "tool_call_name": "write"})
    for _ in range(1000):
        renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "one", "delta": "x" * 1000})
    renderer.ingest("TOOL_CALL_END", {"tool_call_id": "one"})
    renderer.ingest("TOOL_CALL_RESULT", {"content": "\n".join(["line"] * 100)})
    assert len(renderer.drain()) < 2048
    assert "Arguments exceed display budget" in "".join(block.source for block in renderer.transcript.blocks.values())


def test_tool_streams_are_correlated_bounded_and_do_not_override_root_cancellation() -> None:
    status = Status(mode="detailed")
    renderer = StreamRenderer(status, limit=128)
    for run_id in ("child-a", "child-b"):
        renderer.ingest("TOOL_CALL_START", {"tool_call_id": "one", "tool_call_name": "edit"}, child=True, run_id=run_id)
        renderer.ingest("TOOL_CALL_END", {"tool_call_id": "one"}, child=True, run_id=run_id)
    for run_id in ("child-b", "child-a"):
        renderer.ingest("TOOL_CALL_RESULT", {"tool_call_id": "one", "content": "done"}, child=True, run_id=run_id)
    result = renderer.drain()
    assert "edit | returned" in result and "child-a" in result
    assert "child-b" in result
    for index in range(256):
        renderer.ingest("TOOL_CALL_START", {"tool_call_id": str(index), "tool_call_name": "edit"})
    assert len(renderer._tools) == 128
    renderer.ingest("TEXT_MESSAGE_CONTENT", {"delta": "answer"})
    assert status.state == "responding"
    status.state = "cancelling"
    renderer.ingest("TOOL_CALL_START", {"tool_call_id": "late", "tool_call_name": "edit"})
    renderer.ingest("TEXT_MESSAGE_CONTENT", {"delta": "late answer"})
    assert status.state == "cancelling"


def test_setup_choices_expand_to_explicit_native_context_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a13n_harness_ui.interactive.setup.local_sandbox_supported", lambda: True)
    wizard = SetupWizard(advanced=True)
    wizard.accept("codex")
    wizard.accept("gpt-5.6-sol")
    for value in ("on", "all", "extended", "medium", "", "no", "", "sandbox"):
        wizard.accept(value)
    assert wizard.question is None
    selection = wizard.selection("/tmp")
    assert selection["codex_model"] == "gpt-5.6-sol"
    assert selection["codex_context_window"] == 872000
    assert selection["proactive_context_management_threshold"] == 0.65
    assert selection["compact_threshold"] == 0.9
    assert selection["environment_profile"] == "environment-sandbox"
    assert selection["codex_thinking"] == "medium"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--help"],
        ["--version"],
        ["login", "--help"],
        ["run", "--help"],
        ["add", "--help"],
        ["add", "agent", "--help"],
        ["add", "model", "--help"],
    ],
)
def test_cli_help_never_imports_runtime(arguments: list[str], tmp_path: Path) -> None:
    script = f"""
import sys
from a13n_harness_ui.cli import main
main({arguments!r})
for prefix in ('a13n_harness', 'pydantic', 'sqlalchemy', 'textual', 'fastapi', 'prompt_toolkit', 'a13n_harness_ui.app'):
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
    assert not (tmp_path / ".a13n-harness-ui").exists()


async def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    (tmp_path / "codex").mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = tmp_path / "config" / "a13n-harness-ui.yaml"
    selection = SetupSelection(
        providers=("codex",),
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
async def test_cross_directory_resume_reassigns_thread_without_retargeting_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = await _seed(tmp_path, monkeypatch)
    nested = tmp_path / "nested"
    nested.mkdir()
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        first = await app.ensure_cwd_project(nested)
        second = await app.ensure_cwd_project(nested)
        assert first == second
        assert first != "project-local"
        configuration = await app.current_configuration()
        assert configuration.projects["project-local"].roots[0].path == str(tmp_path)
        assert configuration.projects[first].roots[0].path == str(nested)
        backend = SessionBackend(app, CliRequest(), nested, Status())
        thread_id = await backend.ensure_session()
        before = await app.get_thread(thread_id)
        other = SessionBackend(app, CliRequest(thread_id=thread_id), tmp_path, Status())
        assert await other.initialize()
        after = await app.get_thread(thread_id)
        assert after.thread.configuration.project_id == "project-local"
        assert after.thread.configuration.version == before.thread.configuration.version + 1
        assert after.thread.configuration.model_dump(exclude={"project_id", "version"}) == (
            before.thread.configuration.model_dump(exclude={"project_id", "version"})
        )
        assert after.continuation_id == before.continuation_id
        assert other.thread_id == thread_id
        assert thread_id not in {item.thread_id for item in (await backend.resume_sessions()).threads}
        assert thread_id in {item.thread_id for item in (await other.resume_sessions()).threads}
        assert (await app.current_configuration()).projects == configuration.projects


@pytest.mark.anyio
@pytest.mark.parametrize("historical_project", ["existing", "missing", "none"])
async def test_resume_creates_cwd_project_and_preserves_history_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, historical_project: str
) -> None:
    import a13n_harness.model_auth as runtime
    import yaml
    from a13n_harness_ui.storage import ThreadConfigurationMutation, ThreadConfigurationPatch
    from pydantic_ai.models.function import DeltaToolCall

    path = await _seed(tmp_path, monkeypatch)
    model_path = path.parent / "models/codex.yaml"
    document = yaml.safe_load(model_path.read_text())
    native_settings = {
        "openai_service_tier": "priority",
        "stop_sequences": ["  END  "],
        "extra_body": {"properties": {"password": {"description": "  keep spacing  "}}},
    }
    document["settings"].update(native_settings)
    model_path.write_text(yaml.safe_dump(document))
    directory = tmp_path / "new-work"
    directory.mkdir()
    (directory / "AGENTS.md").write_text("NEW DIRECTORY GUIDANCE")
    calls = []

    async def stream(messages, info):
        for key, value in native_settings.items():
            assert info.model_settings[key] == value
        calls.append(messages)
        if len(calls) % 2:
            yield {
                0: DeltaToolCall(
                    name="shell_exec",
                    json_args=json.dumps({"command": "echo current-directory > resumed-marker.txt"}),
                    tool_call_id=f"write-{len(calls)}",
                )
            }
        else:
            yield "Saved answer"

    monkeypatch.setattr(runtime, "CodexRequestModel", lambda *args, **kwargs: FunctionModel(stream_function=stream))
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        renderer = StreamRenderer(backend.status)
        try:
            assert await backend.execute(renderer, prompt="Original question") == ""
        finally:
            renderer.transcript.close()
        thread_id = backend.thread_id
        assert (tmp_path / "resumed-marker.txt").read_text().strip() == "current-directory"
        if historical_project == "none":
            thread = (await app.get_thread(thread_id)).thread
            await app.update_thread_configuration(
                thread_id=thread_id,
                mutation=ThreadConfigurationMutation(
                    expected_version=thread.configuration.version,
                    patch=ThreadConfigurationPatch(project_id=None),
                ),
            )
        elif historical_project == "missing":
            project = next(
                candidate
                for candidate in (path.parent / "projects").glob("*.yaml")
                if yaml.safe_load(candidate.read_text())["id"] == "project-local"
            )
            project.unlink()
            # Remove the creation default too, leaving a valid resource generation.
            document = yaml.safe_load(path.read_text())
            document["defaults"].pop("project", None)
            path.write_text(yaml.safe_dump(document))
            await app.reload_configuration()
        before = await app.get_thread(thread_id)
        original_files = {item: item.read_bytes() for item in path.parent.rglob("*.yaml")}

    async with open_harness_ui_app(settings, configuration_path=path) as app:
        resumed = SessionBackend(app, CliRequest(thread_id=thread_id), directory, Status())
        assert await resumed.initialize()
        assert len(calls) == 2  # Resume itself neither executes tools nor calls the Model.
        after = await app.get_thread(thread_id)
        project_id = after.thread.configuration.project_id
        assert project_id not in (None, "project-local")
        assert after.thread.configuration.version == before.thread.configuration.version + 1
        assert after.thread.configuration.model_dump(exclude={"project_id", "version"}) == (
            before.thread.configuration.model_dump(exclude={"project_id", "version"})
        )
        assert after.continuation_id == before.continuation_id
        assert after.thread.metadata_version == before.thread.metadata_version
        assert "Original question" in await _retained_history(resumed)
        assert "Saved answer" in await _retained_history(resumed)
        assert not (directory / "resumed-marker.txt").exists()
        assert all(item.read_bytes() == content for item, content in original_files.items())
        assert (await app.current_configuration()).projects[project_id].roots[0].path == str(directory)
        renderer = StreamRenderer(resumed.status)
        try:
            assert await resumed.execute(renderer, prompt="Continue in the new directory") == ""
        finally:
            renderer.transcript.close()
        assert (directory / "resumed-marker.txt").read_text().strip() == "current-directory"
        assert "Original question" in str(calls[2])
        assert "NEW DIRECTORY GUIDANCE" in str(calls[2])

    async with open_harness_ui_app(settings, configuration_path=path) as app:
        resumed = SessionBackend(app, CliRequest(thread_id=thread_id), directory, Status())
        assert await resumed.initialize()
        saved = (await app.get_thread(thread_id)).thread.configuration
        assert saved.project_id == project_id
        assert saved.version == after.thread.configuration.version
        assert len(calls) == 4


@pytest.mark.anyio
async def test_cwd_project_creation_preserves_destination_owned_by_another_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    from a13n_harness_ui.errors import AppStateError

    path = await _seed(tmp_path, monkeypatch)
    nested = tmp_path / "nested"
    nested.mkdir()
    generated_id = "project-cwd-" + hashlib.sha256(str(nested.resolve()).encode()).hexdigest()[:20]
    occupied = path.parent / "projects" / f"{generated_id}.yaml"
    content = json.dumps(
        {
            "schema_version": "1",
            "kind": "project",
            "id": "project-unrelated",
            "name": "Unrelated",
            "roots": [{"path": str(tmp_path)}],
        }
    )
    occupied.write_text(content)
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        with pytest.raises(AppStateError) as error:
            await app.ensure_cwd_project(nested)
        assert error.value.code == "project_conflict"
        assert occupied.read_text() == content
        assert "project-unrelated" in (await app.current_configuration()).projects


@pytest.mark.anyio
@pytest.mark.parametrize("generated", [False, True])
async def test_cli_reuses_and_resumes_projects_after_adding_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generated: bool
) -> None:
    import yaml
    from a13n_harness_ui.composition import ThreadCompositionSelection

    path = await _seed(tmp_path, monkeypatch)
    directory = tmp_path / "code" if generated else tmp_path
    directory.mkdir(exist_ok=True)
    extra = tmp_path / "notes"
    extra.mkdir()
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), directory, Status())
        thread_id = await backend.ensure_session()
        project_id = (await app.get_thread(thread_id)).thread.configuration.project_id
        project_file = next(
            candidate
            for candidate in (path.parent / "projects").glob("*.yaml")
            if yaml.safe_load(candidate.read_text())["id"] == project_id
        )
        before = await app.current_configuration()
        selection = ThreadCompositionSelection(
            thread_id=thread_id,
            version=1,
            project_id=project_id,
            agent_source_kind="agent",
            agent_source_id="agent-codex",
            environment_profile_id="environment-native",
            harness_plugin_ids=(),
            environment_run_extension_ids=(),
            mcp_server_ids=(),
        )
        resolver = AgentCompositionResolver(HarnessUiExtensionCatalog())
        captured = resolver.resolve_run(before, selection)
        document = yaml.safe_load(project_file.read_text())
        document["roots"].append({"path": str(extra)})
        project_file.write_text(yaml.safe_dump(document))
        source_bytes = project_file.read_bytes()
        await app.reload_configuration()

        assert await app.ensure_cwd_project(directory) == project_id
        assert await app.cwd_project_ids(directory) == (project_id,)
        assert await app.cwd_project_ids(extra) == ()
        after = await app.current_configuration()
        assert set(after.projects) == set(before.projects)
        assert tuple(root.path for root in after.projects[project_id].roots) == (str(directory), str(extra))
        assert captured.project_roots == (str(directory),)
        assert resolver.resolve_run(after, selection).project_roots == (str(directory), str(extra))

        resumed = SessionBackend(app, CliRequest(), directory, Status())
        assert thread_id in {item.thread_id for item in (await resumed.resume_sessions()).threads}
        await resumed.resume(thread_id)
        assert await resumed.ensure_session() == thread_id
        fresh = SessionBackend(app, CliRequest(), directory, Status())
        fresh_id = await fresh.ensure_session()
        assert (await app.get_thread(fresh_id)).thread.configuration.project_id == project_id
        assert project_file.read_bytes() == source_bytes


@pytest.mark.anyio
async def test_ambiguous_cwd_projects_require_explicit_resume_without_creating_another_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml
    from a13n_harness_ui.errors import AppStateError

    path = await _seed(tmp_path, monkeypatch)
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        thread_id = await backend.ensure_session()
        duplicate = {
            "schema_version": "1",
            "kind": "project",
            "id": "project-another",
            "name": "Another",
            "roots": [{"path": str(tmp_path)}],
        }
        (path.parent / "projects/another.yaml").write_text(yaml.safe_dump(duplicate))
        await app.reload_configuration()
        with pytest.raises(AppStateError) as exc:
            await app.ensure_cwd_project(tmp_path)
        assert exc.value.code == "project_ambiguous"
        assert len((await app.current_configuration()).projects) == 2
        resumed = SessionBackend(app, CliRequest(), tmp_path, Status())
        await resumed.resume(thread_id)
        assert resumed.thread_id == thread_id


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["ambiguous", "active", "archived", "conflict"])
async def test_cross_directory_resume_failure_preserves_saved_and_local_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from unittest.mock import AsyncMock

    import yaml
    from a13n_harness_ui.errors import HarnessUiError
    from a13n_harness_ui.storage import ThreadConfigurationMutation, ThreadConfigurationPatch
    from a13n_harness_ui.surfaces import ThreadMetadataMutation, ThreadMetadataPatch

    path = await _seed(tmp_path, monkeypatch)
    directory = tmp_path / "elsewhere"
    directory.mkdir()
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        original = SessionBackend(app, CliRequest(), tmp_path, Status())
        target = await original.ensure_session()
        backend = SessionBackend(app, CliRequest(), directory, Status())
        current = await backend.ensure_session()
        before = (await app.get_thread(target)).thread
        if failure == "ambiguous":
            (path.parent / "projects/duplicate.yaml").write_text(
                yaml.safe_dump(
                    {
                        "schema_version": "1",
                        "kind": "project",
                        "id": "project-duplicate",
                        "name": "Duplicate",
                        "roots": [{"path": str(directory)}],
                    }
                )
            )
            await app.reload_configuration()
        elif failure == "active":
            monkeypatch.setattr(app, "active_root_operation", AsyncMock(return_value=object()))
        elif failure == "archived":
            await app.update_thread_metadata(
                thread_id=target,
                mutation=ThreadMetadataMutation(
                    expected_version=before.metadata_version,
                    patch=ThreadMetadataPatch(archived=True),
                ),
            )
        else:
            # Simulate another surface updating the head after resume read it.
            context_usage = app.context_usage

            async def conflicting_usage(thread_id):
                await app.update_thread_configuration(
                    thread_id=thread_id,
                    mutation=ThreadConfigurationMutation(
                        expected_version=before.configuration.version,
                        patch=ThreadConfigurationPatch(project_id=None),
                    ),
                )
                return await context_usage(thread_id)

            monkeypatch.setattr(app, "context_usage", conflicting_usage)
        projects = (await app.current_configuration()).projects
        status = (backend.status.session_id, backend.status.agent, backend.status.context_tokens)
        with pytest.raises((ValueError, HarnessUiError)) as exc:
            await backend.resume(target)
        if failure == "ambiguous":
            assert exc.value.code == "project_ambiguous"
        elif failure == "active":
            assert "already running" in str(exc.value)
        elif failure == "archived":
            assert "non-archived root" in str(exc.value)
        else:
            assert "version" in str(exc.value).lower()
        saved = (await app.get_thread(target)).thread.configuration
        if failure == "conflict":
            assert saved.project_id is None
            assert saved.version == before.configuration.version + 1
        else:
            assert saved == before.configuration
        assert backend.thread_id == current
        assert (backend.status.session_id, backend.status.agent, backend.status.context_tokens) == status
        assert backend.resumed_transcript is None
        assert (await app.current_configuration()).projects == projects


@pytest.mark.anyio
async def test_global_and_exact_cwd_guidance_reach_the_first_model_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_harness.model_auth as runtime

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
    import a13n_harness.model_auth as runtime

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
async def test_model_selection_is_remembered_for_project_and_preserves_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_harness.model_auth as runtime
    import yaml

    path = await _seed(tmp_path, monkeypatch)
    model_path = path.parent / "models/alternate.yaml"
    model = yaml.safe_load((path.parent / "models/codex.yaml").read_text())
    model.update(id="model-alternate", name="Alternate")
    model_path.write_text(yaml.safe_dump(model))
    before = {item: item.read_bytes() for item in path.parent.rglob("*.yaml")}

    async def stream(messages, info):
        yield "Reply from the selected model."

    monkeypatch.setattr(runtime, "CodexRequestModel", lambda *a, **kw: FunctionModel(stream_function=stream))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.initialize()
        thread_id = await backend.ensure_session()
        original = (await app.get_thread(thread_id)).thread.configuration
        choices = await backend.choices("model")
        assert {item.value for item in choices} == {"default", "model-codex", "model-alternate"}
        await backend.thinking("low")
        assert (await backend.models("model-alternate")).endswith(" · remembered for this project")
        assert backend.overrides.model_id == "model-alternate"
        assert backend.overrides.thinking is None
        assert (await app.get_thread(thread_id)).thread.configuration.agent_source == original.agent_source
        with pytest.raises(ValueError, match="Unknown model"):
            await backend.models("missing")
        assert backend.overrides.model_id == "model-alternate"
        assert await backend.execute(StreamRenderer(backend.status), prompt="Hello") == ""
        assert (await app.context_usage(thread_id)).model_id == "model-alternate"
        assert backend.status.requests == 1
        assert (await app.get_thread(thread_id)).thread.configuration == original
        await backend.agents("agent-codex")
        assert backend.overrides.model_id == "model-alternate"
        await backend.new()
        assert backend.overrides.model_id == "model-alternate"
        assert backend.status.requests == 0
        await backend.resume(thread_id)
        assert backend.overrides.model_id == "model-alternate"
        assert backend.status.requests == 1
        await backend.execute(StreamRenderer(backend.status), prompt="Continue")
        assert backend.status.requests == 2
        assert (await app.thread_usage(thread_id=thread_id)).root.model_requests == 2
        fresh = SessionBackend(app, CliRequest(thread_id=thread_id), tmp_path, Status())
        await fresh.initialize()
        assert fresh.overrides.model_id == "model-alternate"
        assert fresh.status.requests == 2
        from a13n_harness_ui.cli_runtime import _run_one_shot

        monkeypatch.chdir(tmp_path)
        assert await _run_one_shot(app, CliRequest(command="run", thread_id=thread_id, prompt="Automated")) == 0
        assert (await app.context_usage(thread_id)).model_id == "model-codex"
        assert (await app.cwd_model_preference(tmp_path))[1] == "model-alternate"
        await backend.models("default")
        assert backend.overrides.model_id is None
        assert backend.status.agent == fresh.status.agent
    assert {item: item.read_bytes() for item in before} == before


@pytest.mark.anyio
async def test_cancel_is_receipt_owned_and_session_is_reusable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_harness.model_auth as runtime

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
        job = asyncio.create_task(backend.execute(renderer, prompt="Wait"))
        await asyncio.wait_for(entered.wait(), timeout=5)
        await backend.cancel()
        result = await asyncio.wait_for(job, timeout=5)
        assert "cancel" in result.lower()
        assert "Never reached" not in renderer.drain()
        assert await app.active_root_operation(backend.thread_id) is None


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
    import a13n_harness.model_auth as runtime
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
    from a13n_harness_ui.live import LiveEvent, root_context_samples

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
@pytest.mark.parametrize("decision", ["approve", "deny"])
@pytest.mark.parametrize("mode", ["full-control", "sandbox"])
@pytest.mark.parametrize("shortcut", [True, False])
async def test_pending_shell_decision_is_reviewed_and_resumed_through_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision: str, mode: str, shortcut: bool
) -> None:
    if mode == "sandbox" and os.environ.get("A13N_HARNESS_UI_TEST_SANDBOX") != "1":
        pytest.skip("Set A13N_HARNESS_UI_TEST_SANDBOX=1 with a compatible envd binary and native isolation support")
    import a13n_harness.model_auth as runtime
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
@pytest.mark.parametrize("disable_media", [False, True])
async def test_setup_model_view_uses_declared_media_without_an_external_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disable_media: bool
) -> None:
    import a13n_harness.model_auth as runtime
    import yaml
    from pydantic_ai import BinaryContent
    from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
    from pydantic_ai.models.function import DeltaToolCall

    path = await _seed(tmp_path, monkeypatch)
    model_path = path.parent / "models/codex.yaml"
    document = yaml.safe_load(model_path.read_text(encoding="utf-8"))
    assert document["model_characteristics"]["capabilities"] == ["image_understanding"]
    if disable_media:
        document["model_characteristics"]["capabilities"] = []
        model_path.write_text(yaml.safe_dump(document), encoding="utf-8")
    data = b"\x89PNG"
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
        if isinstance(part, UserPromptPart) and not isinstance(part.content, str)
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

    import a13n_harness.model_auth as runtime
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
    import asyncio

    import a13n_harness.model_auth as runtime
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
    from a13n_harness.model_context import ModelInputEvent
    from a13n_stream_protocol import ContentMetadata, HarnessAguiObserver
    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.messages import EnqueuedMessagesEvent, TextContent

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
                    if isinstance(source.event, ModelInputEvent):
                        batches.append(
                            [
                                item.content if isinstance(item, TextContent) else item
                                for item in source.event.content
                                if not isinstance(item, TextContent)
                                or ContentMetadata.from_native(item.metadata).display
                            ]
                        )
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
async def test_default_codeact_executes_and_disabled_questions_are_not_exposed(tmp_path: Path, monkeypatch) -> None:
    import a13n_harness.model_auth as runtime
    import yaml
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall

    path = await _seed(tmp_path, monkeypatch)
    root = yaml.safe_load(path.read_text())
    root["tools"]["enable_ask_user_question"] = False
    path.write_text(yaml.safe_dump(root))

    async def stream(messages, info):
        names = {tool.name for tool in info.function_tools}
        assert {"run_code", "run_program", "store", "load", "forget"} <= names
        assert "ask_user_question" not in names
        results = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "run_code"
        ]
        if not results:
            yield {0: DeltaToolCall(name="run_code", tool_call_id="code-one", json_args='{"code":"1 + 1"}')}
        else:
            assert "2" in str(results[0].content)
            yield "CodeAct completed."

    monkeypatch.setattr(runtime, "CodexRequestModel", lambda *args, **kwargs: FunctionModel(stream_function=stream))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        renderer = StreamRenderer(backend.status)
        assert await backend.execute(renderer, prompt="Calculate with CodeAct") == ""
        assert "CodeAct completed" in "".join(block.source for block in renderer.transcript.blocks.values())
        renderer.transcript.close()


@pytest.mark.anyio
@pytest.mark.parametrize("timeout", [False, True])
async def test_default_tasks_and_questions_suspend_resume_through_native_ui(
    tmp_path: Path, monkeypatch, timeout: bool
) -> None:
    import a13n_harness.model_auth as runtime
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
            yield "Choice applied."

    monkeypatch.setattr(runtime, "CodexRequestModel", lambda *args, **kwargs: FunctionModel(stream_function=stream))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        renderer = StreamRenderer(backend.status)
        assert await backend.execute(renderer, prompt="Create a task and ask") == ""
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


@pytest.mark.anyio
async def test_codeact_values_survive_ui_continuation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_harness.model_auth as runtime
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall

    path = await _seed(tmp_path, monkeypatch)
    requests = 0

    async def stream(messages, info):
        nonlocal requests
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
        assert await first.execute(StreamRenderer(first.status), prompt="Store the value") == ""
        resumed = SessionBackend(app, CliRequest(thread_id=first.thread_id), tmp_path, Status())
        await resumed.initialize()
        assert await resumed.execute(StreamRenderer(resumed.status), prompt="Load the saved value") == ""
    assert requests == 4


@pytest.mark.anyio
async def test_agent_switch_changes_full_recipe_keeps_history_and_survives_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_harness.model_auth as runtime
    from a13n_harness_ui.interactive.commands import CommandRegistry

    path = await _seed(tmp_path, monkeypatch)
    second = SetupSelection(
        providers=("codex",),
        codex_model="gpt-6-astra",
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
    import asyncio

    import a13n_harness.model_auth as runtime
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
        assert status.usage.cost is None
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
    import a13n_harness.model_auth as runtime
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
    import a13n_harness.model_auth as runtime
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
            assert "Fast (priority)" in await backend.fast(None)
            await backend.thinking("medium")
            assert backend.overrides.thinking == "medium"
            assert backend.overrides.service_tier == "priority"
            await backend.execute(renderer, prompt="Priority please")
            await backend.fast("reset")
            assert backend.overrides.service_tier is None
            assert backend.status.service_tier == "priority"  # Reset is not an off switch.
            await backend.fast("off")
            await backend.resume(thread_id)
            assert backend.overrides.service_tier == "default"
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
            assert backend.overrides.service_tier == "default"
            await backend.models("default")
            assert backend.overrides.service_tier is None
            assert backend.status.service_tier == "priority"
        finally:
            renderer.transcript.close()
    assert observed == ["default", "priority"]
    assert all(item.read_bytes() == contents for item, contents in original.items())


@pytest.mark.anyio
async def test_resume_browser_end_to_end_with_saved_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_harness.model_auth as runtime
    from a13n_harness_ui.interactive.shell import CliShell
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    path = await _seed(tmp_path, monkeypatch)
    calls = []

    async def stream(messages, info):
        calls.append(messages)
        yield "The saved answer to find"

    async def until(predicate):
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0.01)

    monkeypatch.setattr(runtime, "CodexRequestModel", lambda *args, **kwargs: FunctionModel(stream_function=stream))
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.initialize()
        renderer = StreamRenderer(backend.status)
        try:
            await backend.execute(renderer, prompt="Find this original question")
        finally:
            renderer.transcript.close()
        target = backend.thread_id
        # The target is outside the initial result page; search must reach stored metadata.
        for _ in range(21):
            await backend.new()
            await backend.ensure_session()
        current = backend.thread_id
        assert target not in {item.thread_id for item in (await backend.resume_sessions()).threads}
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            shell = CliShell(CliRequest(), directory=tmp_path, status=backend.status)
            shell.backend = backend
            terminal = asyncio.create_task(shell.app.run_async())
            try:
                await until(lambda: shell.app.is_running)
                shell.open_resume()
                await until(lambda: shell.resume_browser.selected is not None)
                browser = shell.resume_browser
                pipe.send_text("saved answer")
                await until(lambda: browser.selected is not None and browser.selected.thread_id == target)
                assert browser.page.total == 1 and "original question" in browser.preview()
                assert backend.thread_id == current and len(calls) == 1
                pipe.send_text("\x14")
                await until(lambda: shell.history_browser is not None and shell.history_browser.page is not None)
                assert backend.thread_id == current
                pipe.send_text("q")
                await until(lambda: shell.history_browser is None)
                pipe.send_text("\x1bOQNamed session\r")
                await until(
                    lambda: (
                        browser.renaming is None and not browser.loading and browser.selected.title == "Named session"
                    )
                )
                assert len(calls) == 1
                pipe.send_text("\r")
                await until(lambda: shell.resume_browser is None and not shell.busy)
                assert backend.thread_id == target
                assert "The saved answer" in "\n".join(
                    block.source for block in shell.renderer.transcript.blocks.values()
                )
                assert len(calls) == 1
            finally:
                shell.close_history()
                shell.close_resume()
                shell.app.exit()
                await terminal
                shell.renderer.transcript.close()
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        page = await backend.resume_sessions(query="Named session")
        assert page.total == 1 and page.threads[0].thread_id == target
        assert page.threads[0].excerpt.first_input == "Find this original question"
        await backend.resume(target)
        assert backend.thread_id == target and len(calls) == 1


@pytest.mark.anyio
async def test_resume_usage_read_failure_keeps_current_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    path = await _seed(tmp_path, monkeypatch)
    directory = tmp_path / "other"
    directory.mkdir()
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        original = SessionBackend(app, CliRequest(), tmp_path, Status())
        target = await original.ensure_session()
        before = (await app.get_thread(target)).thread.configuration
        backend = SessionBackend(app, CliRequest(), directory, Status())
        await backend.initialize()
        current = await backend.ensure_session()
        projects = (await app.current_configuration()).projects
        status = (backend.status.session_id, backend.status.agent, backend.status.context_tokens)
        monkeypatch.setattr(app, "context_usage", AsyncMock(side_effect=OSError("read failed")))
        with pytest.raises(OSError, match="read failed"):
            await backend.resume(target)
        assert backend.thread_id == current
        assert (backend.status.session_id, backend.status.agent, backend.status.context_tokens) == status
        assert backend.resumed_transcript is None
        assert (await app.get_thread(target)).thread.configuration == before
        assert (await app.current_configuration()).projects == projects


@pytest.mark.anyio
async def test_project_model_memory_survives_restart_without_creating_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml

    path = await _seed(tmp_path, monkeypatch)
    model = yaml.safe_load((path.parent / "models/codex.yaml").read_text())
    model.update(id="model-alternate", name="Alternate")
    (path.parent / "models/alternate.yaml").write_text(yaml.safe_dump(model))
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    directory = tmp_path / "new-project"
    directory.mkdir()
    before = {item: item.read_bytes() for item in path.parent.rglob("*.yaml")}
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        backend = SessionBackend(app, CliRequest(), directory, Status())
        await backend.initialize()
        await backend.models("model-alternate")
        await backend.thinking("low")
        await backend.fast("on")
        assert backend.thread_id is None
        assert await app.cwd_project_ids(directory) == ()
        assert {item: item.read_bytes() for item in path.parent.rglob("*.yaml")} == before

    async with open_harness_ui_app(settings, configuration_path=path) as app:
        fresh = SessionBackend(app, CliRequest(), directory, Status())
        await fresh.initialize()
        assert fresh.overrides.model_id == "model-alternate"
        assert fresh.overrides.thinking is None
        assert fresh.overrides.service_tier is None
        assert await app.cwd_project_ids(directory) == ()
        # Neither explicit launch selection nor automation consumes or erases memory.
        for request in (CliRequest(agent_id="agent-codex"), CliRequest(command="run")):
            explicit = SessionBackend(app, request, directory, Status())
            await explicit.initialize()
            assert explicit.overrides.model_id is None
        other = SessionBackend(app, CliRequest(), tmp_path, Status())
        await other.initialize()
        assert other.overrides.model_id is None
        scope, remembered = await app.cwd_model_preference(directory)
        assert remembered == "model-alternate"
        thread_id = await fresh.ensure_session()
        assert (await app.get_thread(thread_id)).thread.configuration.project_id == scope
        # Clearing is durable, not merely a one-window override.
        assert "preference cleared" in await fresh.models("default")
        reset = SessionBackend(app, CliRequest(thread_id=thread_id), directory, Status())
        await reset.initialize()
        assert reset.overrides.model_id is None
        assert await app.cwd_model_preference(directory) == (scope, None)


@pytest.mark.anyio
async def test_project_model_memory_resume_uses_launch_project_and_only_explicit_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = await _seed(tmp_path, monkeypatch)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    directory = tmp_path / "other-project"
    directory.mkdir()
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        first = SessionBackend(app, CliRequest(), tmp_path, Status())
        await first.initialize()
        thread_id = await first.ensure_session()
        await first.models("model-codex")
        second = SessionBackend(app, CliRequest(), tmp_path, Status())
        await second.initialize()
        assert second.overrides.model_id == "model-codex"
        await second.models("default")
        # An already open terminal retains its selection but must not write it back.
        await first.new()
        await first.resume(thread_id)
        assert first.overrides.model_id == "model-codex"
        fresh = SessionBackend(app, CliRequest(thread_id=thread_id), tmp_path, Status())
        await fresh.initialize()
        assert fresh.overrides.model_id is None
        other = SessionBackend(app, CliRequest(), directory, Status())
        await other.initialize()
        await other.models("model-codex")
        moved = SessionBackend(app, CliRequest(thread_id=thread_id), directory, Status())
        await moved.initialize()
        assert moved.overrides.model_id == "model-codex"
        assert (await app.get_thread(thread_id)).thread.configuration.project_id in await app.cwd_project_ids(directory)
        assert (await app.cwd_model_preference(tmp_path))[1] is None


@pytest.mark.anyio
async def test_missing_project_model_warns_and_failed_selection_preserves_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    import yaml

    path = await _seed(tmp_path, monkeypatch)
    model_path = path.parent / "models/alternate.yaml"
    model = yaml.safe_load((path.parent / "models/codex.yaml").read_text())
    model.update(id="model-alternate", name="Alternate")
    model_path.write_text(yaml.safe_dump(model))
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.initialize()
        await backend.models("model-alternate")
        before = backend.overrides
        with monkeypatch.context() as patch:
            patch.setattr(app, "remember_project_model", AsyncMock(side_effect=OSError("write failed")))
            with pytest.raises(OSError, match="write failed"):
                await backend.models("default")
        assert backend.overrides == before
        assert (await app.cwd_model_preference(tmp_path))[1] == "model-alternate"
    model_path.unlink()
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.initialize()
        assert backend.overrides.model_id is None
        assert len(backend.status.notices) == 1
        assert "model-alternate" in backend.status.notices[0]
        await backend.refresh()
        await backend.new()
        assert len(backend.status.notices) == 1
        # A fallback is not a new user preference and cannot overwrite another window.
        assert (await app.cwd_model_preference(tmp_path))[1] == "model-alternate"


@pytest.mark.anyio
async def test_model_memory_disambiguates_projects_on_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import yaml
    from a13n_harness_ui.surfaces import NewThreadDefaults

    path = await _seed(tmp_path, monkeypatch)
    projects = path.parent / "projects"
    projects.mkdir(exist_ok=True)
    for name in ("a", "b"):
        (projects / f"{name}.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": "1",
                    "kind": "project",
                    "id": f"project-{name}",
                    "name": name,
                    "roots": [{"path": str(tmp_path)}],
                }
            )
        )
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        a = await app.create_thread(defaults=NewThreadDefaults(project_id="project-a"))
        b = await app.create_thread(defaults=NewThreadDefaults(project_id="project-b"))
        await app.remember_project_model(project_id="project-a", model_id="model-codex")
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.initialize()
        assert backend.overrides.model_id is None
        with pytest.raises(ValueError, match="Multiple Projects"):
            await backend.models("model-codex")
        await backend.resume(a.thread_id)
        assert backend.overrides.model_id == "model-codex"
        await backend.resume(b.thread_id)
        assert backend.overrides.model_id is None
        await backend.models("model-codex")
        await backend.models("default")
        assert await app.cwd_model_preference(tmp_path, project_id="project-a") == ("project-a", "model-codex")
        assert await app.cwd_model_preference(tmp_path, project_id="project-b") == ("project-b", None)
