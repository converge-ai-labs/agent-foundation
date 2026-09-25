"""Terminal resume, cwd Projects and remembered Model selection through the real App."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.composition import AgentCompositionResolver
from a13n_harness_ui.extensions import HarnessUiExtensionCatalog
from a13n_harness_ui.interactive.backend import SessionBackend
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from pydantic_ai.models.function import FunctionModel

from .test_interactive import _retained_history, _seed, no_real_model_requests  # noqa: F401  (autouse guard)


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
        assert after.thread.configuration.model_dump(
            exclude={"project_id", "version", "local_roots", "default_environment"}
        ) == (
            before.thread.configuration.model_dump(
                exclude={"project_id", "version", "local_roots", "default_environment"}
            )
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
    import a13n_harness.models.codex as runtime
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
        assert after.thread.configuration.model_dump(
            exclude={"project_id", "version", "local_roots", "default_environment"}
        ) == (
            before.thread.configuration.model_dump(
                exclude={"project_id", "version", "local_roots", "default_environment"}
            )
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
            local_roots=(str(directory),),
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
        assert resolver.resolve_run(after, selection).project_roots == (str(directory),)

        resumed = SessionBackend(app, CliRequest(), directory, Status())
        assert thread_id in {item.thread_id for item in (await resumed.resume_sessions()).threads}
        await resumed.resume(thread_id)
        assert await resumed.ensure_session() == thread_id
        fresh = SessionBackend(app, CliRequest(), directory, Status())
        fresh_id = await fresh.ensure_session()
        assert (await app.get_thread(fresh_id)).thread.configuration.project_id == project_id
        assert (await app.get_thread(fresh_id)).thread.configuration.local_roots == (str(directory), str(extra))
        assert (await app.get_thread(thread_id)).thread.configuration.local_roots == (str(directory),)
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
async def test_model_selection_is_remembered_for_project_and_preserves_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_harness.models.codex as runtime
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
        assert {item.value for item in choices} == {"default", "defaults", "model-codex", "model-alternate"}
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
async def test_resume_browser_end_to_end_with_saved_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_harness.models.codex as runtime
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


@pytest.mark.anyio
async def test_resume_thread_default_outranks_restored_memory_but_not_explicit_choice(tmp_path, monkeypatch):
    import yaml
    from a13n_harness_ui.surfaces import NewThreadDefaults

    path = await _seed(tmp_path, monkeypatch)
    model = yaml.safe_load((path.parent / "models/codex.yaml").read_text())
    model.update(id="model-alternate", name="Alternate")
    (path.parent / "models/alternate.yaml").write_text(yaml.safe_dump(model))
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        creator = SessionBackend(app, CliRequest(), tmp_path, Status())
        await creator.initialize()
        await creator.models("model-alternate")
        project = await app.ensure_cwd_project(tmp_path)
        target = await app.create_thread(defaults=NewThreadDefaults(project_id=project, default_model_id="model-codex"))
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.initialize()
        assert backend.overrides.model_id == "model-alternate"
        await backend.resume(target.thread_id)
        assert backend.overrides.model_id is None
        assert backend.status.model == model["route"]
        # Explicit in-session choice remains an override when switching Threads.
        await backend.models("model-alternate")
        await backend.resume(target.thread_id)
        assert backend.overrides.model_id == "model-alternate"
        fresh = SessionBackend(app, CliRequest(thread_id=target.thread_id), tmp_path, Status())
        await fresh.initialize()
        assert fresh.overrides.model_id is None
        assert fresh.status.model == model["route"]


@pytest.mark.anyio
@pytest.mark.parametrize("remembered_model", [None, "model-codex"])
async def test_resume_saved_default_preserves_session_controls(tmp_path, monkeypatch, remembered_model):
    import yaml
    from a13n_harness_ui.surfaces import NewThreadDefaults

    path = await _seed(tmp_path, monkeypatch)
    model_path = path.parent / "models/codex.yaml"
    model = yaml.safe_load(model_path.read_text())
    model["settings"]["openai_reasoning_mode"] = "pro"
    model_path.write_text(yaml.safe_dump(model))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")), configuration_path=path
    ) as app:
        project = await app.ensure_cwd_project(tmp_path)
        if remembered_model is not None:
            await app.remember_project_model(project_id=project, model_id=remembered_model)
        target = await app.create_thread(defaults=NewThreadDefaults(project_id=project, default_model_id="model-codex"))
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.initialize()
        assert backend.status.reasoning_mode == "pro"
        await backend.pro("off")
        await backend.fast("off")
        await backend.resume(target.thread_id)
        assert backend.overrides.model_id is None
        assert backend.overrides.reasoning_mode == "standard"
        assert backend.status.reasoning_mode == "standard"
        assert backend.overrides.fast is False
