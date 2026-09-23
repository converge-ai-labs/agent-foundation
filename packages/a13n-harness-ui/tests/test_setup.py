from pathlib import Path

import pytest
import yaml
from a13n_harness_ui.composition import AgentCompositionResolver
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.configuration.setup import SetupSelection, preview_setup, publish_setup
from a13n_harness_ui.errors import ConfigurationError
from a13n_harness_ui.extensions import HarnessUiExtensionCatalog
from a13n_harness_ui.model_authoring import ModelRecipeRequest, prepare_model


def _selection(tmp_path: Path, **changes: object) -> SetupSelection:
    return SetupSelection.model_validate(
        {
            "model": prepare_model(ModelRecipeRequest(connection="codex", model_id="gpt-5.6-sol")),
            "default_agent": "agent-codex",
            "project": "project-local",
            "project_path": str(tmp_path),
            "environment_profile": "environment-native",
            **changes,
        }
    )


def _validate():
    return AgentCompositionResolver(HarnessUiExtensionCatalog()).validate_generation


@pytest.mark.anyio
async def test_setup_previews_without_publication_and_seeds_selected_connection(tmp_path: Path) -> None:
    path = tmp_path / "config" / "config.yaml"
    selection = _selection(tmp_path)
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    assert not path.parent.exists()
    root = yaml.safe_load(preview.files[path.name])
    assert root["schema_version"] == "1"
    assert root["webui"] == {"sidekick": {}}
    assert root["tools"] == {
        "enable_ask_user_question": True,
        "interaction_timeout_seconds": 120,
        "enable_codeact": True,
    }
    assert "gpt-5.6-luna" in preview.files["models/codex-review.yaml"]
    result = await publish_setup(path, selection, validate_candidate=_validate())
    assert result.completed
    assert result.published_paths[-1] == path.name
    source = await load_harness_ui_configuration(path)
    assert source.document.defaults.agent == "agent-codex"
    assert yaml.safe_load(path.read_text())["tools"] == root["tools"]
    assert source.document.tools.enable_codeact is True
    assert yaml.safe_load(path.read_text())["webui"] == {"sidekick": {}}
    assert source.document.webui.sidekick is not None
    assert len(source.agents) == 1
    assert source.projects["project-local"].name == tmp_path.name
    assert yaml.safe_load(preview.files["projects/project-local.yaml"])["name"] == tmp_path.name


@pytest.mark.anyio
@pytest.mark.parametrize("tier", [None, "priority", "default"])
@pytest.mark.parametrize("operation", ["setup", "add_model", "add_agent"])
async def test_setup_writes_native_codex_service_tier(tmp_path: Path, tier: str | None, operation: str) -> None:
    path = tmp_path / "config.yaml"
    changes: dict[str, object] = {}
    if operation != "setup":
        assert (await publish_setup(path, _selection(tmp_path), validate_candidate=_validate())).completed
        changes = (
            {"new_model_id": "model-second", "new_model_name": "Second Model"}
            if operation == "add_model"
            else {"new_agent_id": "agent-second", "new_agent_name": "Second Agent"}
        )
    recipe = prepare_model(ModelRecipeRequest(connection="codex", model_id="gpt-5.6-sol"))
    settings = dict(recipe.settings)
    if tier is None:
        settings.pop("openai_service_tier")
    else:
        settings["openai_service_tier"] = tier
    selection = _selection(tmp_path, model=recipe.model_copy(update={"settings": settings}), **changes)
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    models = [yaml.safe_load(content) for name, content in preview.files.items() if name.startswith("models/")]
    settings = next(model["settings"] for model in models if model["settings"].get("thinking") == "high")
    assert "service_tier" not in settings
    if tier is None:
        assert "openai_service_tier" not in settings
    else:
        assert settings["openai_service_tier"] == tier
    assert (await publish_setup(path, selection, validate_candidate=_validate())).completed
    for name, content in preview.files.items():
        assert (path.parent / name).read_text() == content


@pytest.mark.anyio
@pytest.mark.parametrize(
    "authored_tools",
    [
        {},
        {"enable_codeact": False},
        {"enable_ask_user_question": False, "interaction_timeout_seconds": 45},
        {"enable_ask_user_question": False, "interaction_timeout_seconds": 30, "enable_codeact": False},
    ],
)
async def test_setup_materializes_missing_tool_defaults_and_preserves_authored_values(
    tmp_path: Path, authored_tools: dict[str, object]
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"schema_version": "1", "tools": authored_tools}))
    expected = {
        "enable_ask_user_question": True,
        "interaction_timeout_seconds": 120,
        "enable_codeact": True,
        **authored_tools,
    }
    selection = _selection(tmp_path)
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    assert yaml.safe_load(preview.files[path.name])["tools"] == expected
    assert yaml.safe_load(path.read_text())["tools"] == authored_tools
    assert (await publish_setup(path, selection, validate_candidate=_validate())).completed
    assert yaml.safe_load(path.read_text())["tools"] == expected
    assert (await preview_setup(path, selection, validate_candidate=_validate())).files[path.name] == path.read_text()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "webui",
    [{}, {"sidekick": None}, {"sidekick": {}}, {"sidekick": {"agent": "agent-codex", "model": "model-codex"}}],
)
async def test_setup_materializes_sidekick_and_preserves_explicit_choices(tmp_path: Path, webui: dict) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"schema_version": "1", "webui": webui}))
    original = path.read_bytes()
    expected = {"sidekick": {}, **webui}
    selection = _selection(tmp_path)
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    assert yaml.safe_load(preview.files[path.name])["webui"] == expected
    assert path.read_bytes() == original
    assert (await publish_setup(path, selection, validate_candidate=_validate())).completed
    assert yaml.safe_load(path.read_text())["webui"] == expected
    assert (await preview_setup(path, selection, validate_candidate=_validate())).files[path.name] == path.read_text()


@pytest.mark.anyio
async def test_setup_preserves_edited_resources_and_root_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    selection = _selection(tmp_path)
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    assert (await publish_setup(path, selection, validate_candidate=_validate())).completed
    agent = tmp_path / "agents" / "codex.yaml"
    model = tmp_path / "models" / "codex.yaml"
    agent_document = yaml.safe_load(agent.read_text(encoding="utf-8"))
    agent_document["name"] = "My edited agent - 研究"
    original = yaml.safe_dump(agent_document, allow_unicode=True).encode("utf-8")
    agent.write_bytes(original)
    model_document = yaml.safe_load(model.read_text(encoding="utf-8"))
    model_document["name"] = "Existing · Model"
    model_document["model_characteristics"]["capabilities"] = []
    original_model = yaml.safe_dump(model_document, allow_unicode=True).encode("utf-8")
    model.write_bytes(original_model)
    path.write_text(path.read_text(encoding="utf-8") + "process:\n  pricing_auto_update: false\n", encoding="utf-8")
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    assert "agents/codex.yaml" not in preview.files
    assert (await publish_setup(path, selection, validate_candidate=_validate())).completed
    assert agent.read_bytes() == original
    assert model.read_bytes() == original_model
    assert "pricing_auto_update: false" in path.read_text(encoding="utf-8")


@pytest.mark.anyio
@pytest.mark.parametrize("reload_source", ["manual", "observer"])
async def test_setup_publication_serializes_configuration_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reload_source: str
) -> None:
    from a13n_harness_ui import app as app_module
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from anyio import Event, create_task_group, fail_after, sleep_forever, wait_all_tasks_blocked

    path = tmp_path / "config.yaml"
    selection = _selection(tmp_path, model=None, default_agent="agent-default")
    assert (await publish_setup(path, selection, validate_candidate=_validate())).completed
    entered = Event()
    release = Event()
    observer_tick = Event()
    observer_scanned = Event()
    publishing = False
    overlaps = []
    publish = app_module.publish_setup

    async def blocked_publish(*args, **kwargs):
        nonlocal publishing
        publishing = True
        entered.set()
        await release.wait()
        try:
            return await publish(*args, **kwargs)
        finally:
            publishing = False

    async def observe_once(_delay):
        await observer_tick.wait()
        if observer_scanned.is_set():
            await sleep_forever()

    async def fingerprint(*args, **kwargs):
        observer_scanned.set()
        return ((path.name, (0, 0, 1, 1)),)

    monkeypatch.setattr(app_module, "publish_setup", blocked_publish)
    monkeypatch.setattr(app_module, "sleep", observe_once)
    monkeypatch.setattr(app_module, "configuration_tree_fingerprint", fingerprint)
    async with app_module.open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state")), configuration_path=path
    ) as app:
        reload = app._reload_configuration_from_path

        async def tracked_reload():
            overlaps.append(publishing)
            await reload()

        monkeypatch.setattr(app, "_reload_configuration_from_path", tracked_reload)
        with fail_after(5):
            async with create_task_group() as tasks:
                tasks.start_soon(app.apply_setup, selection)
                await entered.wait()
                if reload_source == "manual":
                    tasks.start_soon(app.reload_configuration)
                else:
                    observer_tick.set()
                try:
                    await wait_all_tasks_blocked()
                    assert not overlaps
                finally:
                    release.set()
            if reload_source == "observer":
                await observer_scanned.wait()
        assert not any(overlaps)
        assert (await app.status()).candidate_error_code is None


@pytest.mark.anyio
async def test_setup_uses_current_configuration_after_preview(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    selection = _selection(tmp_path)
    await preview_setup(path, selection, validate_candidate=_validate())
    path.write_text('schema_version: "1"\nprocess: {log_level: DEBUG}\n')
    result = await publish_setup(path, selection, validate_candidate=_validate())
    assert result.completed
    assert "DEBUG" in path.read_text()
    assert (tmp_path / "models").exists()


@pytest.mark.anyio
async def test_setup_partial_publication_is_retryable_without_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness_ui.configuration import setup

    path = tmp_path / "config.yaml"
    selection = _selection(tmp_path)
    await preview_setup(path, selection, validate_candidate=_validate())
    publish = setup._publish_content
    calls = 0

    def fail_second(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("test failure")
        publish(path, content)

    monkeypatch.setattr(setup, "_publish_content", fail_second)
    result = await publish_setup(path, selection, validate_candidate=_validate())
    assert not result.completed
    assert len(result.published_paths) == 1
    assert not path.exists()
    retained = (tmp_path / result.published_paths[0]).read_bytes()
    monkeypatch.setattr(setup, "_publish_content", publish)
    await preview_setup(path, selection, validate_candidate=_validate())
    assert (await publish_setup(path, selection, validate_candidate=_validate())).completed
    assert (tmp_path / result.published_paths[0]).read_bytes() == retained


@pytest.mark.anyio
async def test_setup_rejects_occupied_destination_for_another_resource(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "codex.yaml").write_text(
        'schema_version: "1"\nkind: agent\nid: agent-unrelated\nname: Unrelated\nmodel: model-grok\n'
    )
    with pytest.raises(ConfigurationError, match="belongs to another resource"):
        await preview_setup(path, _selection(tmp_path), validate_candidate=_validate())
    assert not path.exists()


@pytest.mark.anyio
@pytest.mark.parametrize("external_last", [False, True])
async def test_setup_publication_is_last_write_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, external_last: bool
) -> None:
    from a13n_harness_ui.configuration import setup

    path = tmp_path / "config.yaml"
    path.write_text('schema_version: "1"\n')
    selection = _selection(tmp_path)
    publish = setup._publish_content
    external = 'schema_version: "1"\nprocess: {log_level: DEBUG}\n'

    def racing_publish(target: Path, content: bytes) -> None:
        if target == path and not external_last:
            path.write_text(external)
        publish(target, content)
        if target == path and external_last:
            path.write_text(external)

    monkeypatch.setattr(setup, "_publish_content", racing_publish)
    result = await publish_setup(path, selection, validate_candidate=_validate())
    assert result.completed
    assert (path.read_text() == external) is external_last
    if not external_last:
        assert (await load_harness_ui_configuration(path)).document.defaults.agent == "agent-codex"
    assert not list(tmp_path.glob(".a13n-harness-ui-setup-recovery-*"))


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["setup", "add_model", "add_agent"])
async def test_setup_with_global_guidance_saves_successfully(tmp_path: Path, operation: str) -> None:
    path = tmp_path / "config.yaml"
    initial = _selection(tmp_path)
    if operation != "setup":
        assert (await publish_setup(path, initial, validate_candidate=_validate())).completed
    guidance = tmp_path / "AGENTS.md"
    content = "# Guidance\r\nPreserve authored instructions — 研究.\r\n".encode()
    guidance.write_bytes(content)
    selection = (
        initial
        if operation == "setup"
        else _selection(
            tmp_path,
            **(
                {"new_model_id": "model-second", "new_model_name": "Second Model"}
                if operation == "add_model"
                else {"new_agent_id": "agent-second", "new_agent_name": "Second Agent"}
            ),
        )
    )
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    assert "AGENTS.md" in preview.preserved_paths
    result = await publish_setup(path, selection, validate_candidate=_validate())
    assert result.completed, result.error_message
    assert "AGENTS.md" not in result.published_paths
    assert guidance.read_bytes() == content
    if operation == "add_model":
        assert result.published_paths == ("models/second.yaml",)
    assert (await publish_setup(path, selection, validate_candidate=_validate())).completed


@pytest.mark.anyio
async def test_app_setup_discovery_isolates_and_retries_invalid_codex_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "missing-codex-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state")),
        configuration_path=tmp_path / "config.yaml",
    ) as app:
        status = await app.setup_status()
        assert status.needed
        assert status.providers[0].action == "retry"
        assert status.providers[0].diagnostic
        assert status.providers[1].provider == "grok"
        (tmp_path / "missing-codex-home").mkdir()
        status = await app.setup_status(rediscover=True)
        assert status.providers[0].diagnostic is None
        assert not status.providers[0].available
        assert (await app.status()).candidate_error_code is None


@pytest.mark.anyio
async def test_sandbox_preflight_validates_the_actual_publication_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness_ui import app as app_module
    from a13n_harness_ui.errors import AppStateError
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings

    path = tmp_path / "config.yaml"
    initial = _selection(tmp_path, model=None, default_agent="agent-default")
    assert (await publish_setup(path, initial, validate_candidate=_validate())).completed
    original = path.read_bytes()
    unchecked = tmp_path / "unchecked"
    unchecked.mkdir()
    publish = app_module.publish_setup

    async def change_project_then_publish(*args, **kwargs):
        project = tmp_path / "projects" / "project-local.yaml"
        document = yaml.safe_load(project.read_text())
        document["roots"] = [{"path": str(unchecked)}]
        project.write_text(yaml.safe_dump(document))
        return await publish(*args, **kwargs)

    async with app_module.open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state")), configuration_path=path
    ) as app:
        app._sandbox_ready_paths.add(tmp_path.resolve())
        selection = initial.model_copy(update={"environment_profile": "environment-sandbox"})
        await app.preview_setup(selection)
        monkeypatch.setattr(app_module, "publish_setup", change_project_then_publish)
        with pytest.raises(AppStateError) as error:
            await app.apply_setup(selection)
        assert error.value.code == "sandbox_preflight_required"
        assert path.read_bytes() == original
        app._sandbox_ready_paths.add(unchecked.resolve())
        assert (await app.apply_setup(selection)).completed
        assert (await app.current_configuration()).document.defaults.environment_profile == "environment-sandbox"


@pytest.mark.anyio
async def test_native_preflight_never_resolves_envd(tmp_path: Path) -> None:
    from a13n_harness_ui.setup import preflight_environment

    async def forbidden() -> Path:
        pytest.fail("Native must not resolve or probe envd")

    result = await preflight_environment("environment-native", tmp_path, resolve_executable=forbidden)
    assert result.ready and result.code == "full_control"


@pytest.mark.anyio
async def test_sandbox_preflight_uses_production_denied_network_and_does_not_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness.providers.environment.models import EnvironmentError
    from a13n_harness_ui import setup

    observed = []

    async def resolve() -> Path:
        return tmp_path / "envd"

    async def probe(executable, project_path, *, protected_roots, owned_probe_root):
        assert protected_roots == () and owned_probe_root is None
        observed.append((executable, project_path))
        raise EnvironmentError("probe failed", code="provider_unavailable")

    monkeypatch.setattr(setup, "local_sandbox_supported", lambda: True)
    monkeypatch.setattr(setup, "validate_sandbox_runtime", probe)
    result = await setup.preflight_environment("environment-sandbox", tmp_path, resolve_executable=resolve)
    assert not result.ready
    assert result.profile_id == "environment-sandbox"
    assert observed == [(tmp_path / "envd", tmp_path)]
    assert any("Full Control" in instruction for instruction in result.instructions)


@pytest.mark.anyio
async def test_sandbox_preflight_cancellation_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from a13n_harness_ui import setup

    monkeypatch.setattr(setup, "local_sandbox_supported", lambda: True)
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def resolve() -> Path:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return tmp_path / "envd"

    task = asyncio.create_task(setup.preflight_environment("environment-sandbox", tmp_path, resolve_executable=resolve))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.anyio
@pytest.mark.parametrize("review_outcome", ["flagged", "error"])
async def test_codex_setup_routes_shell_review_to_luna_and_applies_default_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    review_outcome: str,
) -> None:
    import json

    import a13n_harness.models.codex as runtime
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    # Both native Model factories are replaced, not the App composition or
    # shell-review pipeline. No compatible account credential is read by a model.
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "codex").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    marker = tmp_path / "executed-command"
    reviewed: list[str | bool | None] = []
    resolved: list[str] = []

    async def main_stream(messages, info):
        assert info.model_request_parameters.thinking == "high"
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            yield "done"
            return
        yield {
            0: DeltaToolCall(
                name="shell_exec",
                json_args=json.dumps({"command": f'echo reviewed > "{marker}"'}),
                tool_call_id="review-call",
            )
        }

    async def review(messages, info):
        reviewed.append(info.model_request_parameters.thinking)
        assert not info.function_tools
        if review_outcome == "error":
            raise RuntimeError("Synthetic reviewer unavailable")
        assert len(info.output_tools) == 1
        assert info.model_settings["tool_choice"] == "auto"
        yield {
            0: DeltaToolCall(
                name=info.output_tools[0].name,
                json_args='{"risk":"extra_high","reason":"Requires user review"}',
            )
        }

    def build(model_name: str, **kwargs):
        resolved.append(model_name)
        return (
            FunctionModel(
                stream_function=review, profile={"supports_thinking": True, "supports_json_object_output": True}
            )
            if model_name.endswith("luna")
            else FunctionModel(stream_function=main_stream, profile={"supports_thinking": True})
        )

    monkeypatch.setattr(runtime, "CodexRequestModel", build)
    path = tmp_path / "config" / "config.yaml"
    selection = _selection(tmp_path)
    await preview_setup(path, selection, validate_candidate=_validate())
    assert (await publish_setup(path, selection, validate_candidate=_validate())).completed
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state")), configuration_path=path
    ) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Try a reviewed command")
        outcome = await app.wait_root_operation(receipt.receipt_id)
        expected_status = "suspended" if review_outcome == "flagged" else "completed"
        assert outcome.status.value == expected_status, (outcome.model_dump_json(), resolved, reviewed)
        batch = await app.thread_decisions(thread_id=thread.thread_id)
        if review_outcome == "flagged":
            assert batch is not None and len(batch.requests) == 1
            assert batch.requests[0].kind == "approval"
        else:
            assert batch is None
    assert set(resolved) == {"gpt-5.6-luna", "gpt-5.6-sol"}
    assert reviewed == ["low"]
    assert marker.exists() is (review_outcome == "error")


@pytest.mark.anyio
async def test_supported_sandbox_readiness_requires_production_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    import a13n_harness_ui.setup as setup

    monkeypatch.setattr(setup, "local_sandbox_supported", lambda: True)
    executable = tmp_path / "a13n-envd"
    resolve = AsyncMock(return_value=executable)
    probe = AsyncMock(side_effect=OSError("required execution isolation is not implemented for this platform"))
    monkeypatch.setattr(setup, "local_sandbox_supported", lambda: True)
    monkeypatch.setattr(setup, "validate_sandbox_runtime", probe)
    result = await setup.preflight_environment("environment-sandbox", tmp_path, resolve_executable=resolve)
    resolve.assert_awaited_once()
    probe.assert_awaited_once()
    assert not result.ready
    assert result.code == "sandbox_probe_failed"
    assert any("explicitly choose Full Control" in item for item in result.instructions)
    probe.side_effect = None
    result = await setup.preflight_environment("environment-sandbox", tmp_path, resolve_executable=resolve)
    assert result.ready and result.code == "sandbox_ready"


def test_windows_full_control_selects_powershell_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_harness_ui.extensions.environment_adapters as adapters

    executable = tmp_path / "pwsh.exe"
    monkeypatch.setattr(adapters.sys, "platform", "win32")
    monkeypatch.setattr(adapters.shutil, "which", lambda name: str(executable) if name == "pwsh" else None)
    assert adapters._host_shell() == executable


@pytest.mark.anyio
async def test_not_now_finishes_setup_without_model_or_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.errors import CompositionError
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = tmp_path / "config" / "config.yaml"
    selection = _selection(tmp_path, model=None, default_agent="agent-default")
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state")), configuration_path=path
    ) as app:
        assert (await app.setup_status()).needed
        preview = await app.preview_setup(selection)
        assert not path.exists()
        assert not any(name.startswith("models/") for name in preview.files)
        assert (await app.apply_setup(selection)).completed
        assert not (await app.setup_status()).needed
        source = await load_harness_ui_configuration(path)
        assert source.agents["agent-default"].model is None
        assert source.agents["agent-default"].instructions == ""
        thread = await app.create_thread()
        try:
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="hello")
        except CompositionError as exc:
            assert exc.code == "agent_model_required"
        else:
            outcome = await app.wait_root_operation(receipt.receipt_id)
            assert outcome.status.value == "failed"
            assert "agent_model_required" in outcome.model_dump_json()
    assert not (tmp_path / "codex" / "auth.json").exists()


@pytest.mark.anyio
async def test_new_user_enters_setup_without_a_root_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.interactive.onboarding import SetupCancelled, run_setup
    from a13n_harness_ui.settings_loader import load_harness_ui_settings

    monkeypatch.delenv("A13N_HARNESS_UI_DATA_ROOT", raising=False)
    monkeypatch.delenv("GROK_AUTH_PATH", raising=False)
    monkeypatch.setenv("GROK_HOME", str(tmp_path / ".grok"))
    source = await load_harness_ui_settings()
    assert not source.exists
    assert not source.explicit
    assert source.configuration is None
    assert source.candidate_error is None
    settings = source.settings.model_copy(update={"pricing_auto_update": False})
    questions = []
    messages = []

    async def cancel_at_first_question(question, prompt):
        questions.append(question)
        raise SetupCancelled()

    async with open_harness_ui_app(
        settings, configuration_path=source.path, configuration_error=source.candidate_error
    ) as app:
        status = await app.setup_status()
        assert status.needed
        assert status.diagnostic is None
        assert not await run_setup(app, tmp_path, ask_user=cancel_at_first_question, emit=messages.append)
        assert len(questions) == 1
        assert not any("Configuration needs repair" in message for message in messages)
        assert not source.path.exists()
        selection = _selection(tmp_path, model=None, default_agent="agent-default")
        assert (await app.apply_setup(selection)).completed
        assert not (await app.setup_status()).needed
    assert source.path.is_file()


@pytest.mark.anyio
async def test_api_key_setup_publishes_only_reference_and_additional_instructions(tmp_path: Path) -> None:
    selection = _selection(
        tmp_path,
        default_agent="agent-api-key",
        model={"route": "openai:gpt-5", "authentication": {"kind": "api_key", "env": "MY_EXISTING_KEY"}},
        instructions="Answer briefly.",
    )
    path = tmp_path / "config.yaml"
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    assert "MY_EXISTING_KEY" in preview.files["models/api-key.yaml"]
    assert "Answer briefly." in preview.files["agents/api-key.yaml"]
    assert "system_prompt" not in preview.files["agents/api-key.yaml"]
    assert (await publish_setup(path, selection, validate_candidate=_validate())).completed
    source = await load_harness_ui_configuration(path)
    assert source.agents["agent-api-key"].model == "model-api-key"
    assert source.models["model-api-key"].authentication.env == "MY_EXISTING_KEY"


@pytest.mark.anyio
async def test_setup_run_delivers_base_and_additions_through_distinct_native_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_harness.models.codex as runtime
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.prompts import DEFAULT_SYSTEM_PROMPT
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from pydantic_ai.messages import ModelRequest, SystemPromptPart
    from pydantic_ai.models.function import FunctionModel

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "codex").mkdir()
    calls = []

    async def stream(messages, info):
        system = [
            p.content
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, SystemPromptPart)
        ]
        assert DEFAULT_SYSTEM_PROMPT in system
        assert "Use short answers." not in system
        assert info.instructions is not None and "Use short answers." in info.instructions
        calls.append(True)
        yield "done"

    monkeypatch.setattr(
        runtime,
        "CodexRequestModel",
        lambda *args, **kwargs: FunctionModel(stream_function=stream, profile={"supports_thinking": True}),
    )
    path = tmp_path / "config" / "config.yaml"
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state")), configuration_path=path
    ) as app:
        selection = _selection(tmp_path, instructions="Use short answers.", shell_review=False)
        await app.preview_setup(selection)
        assert (await app.apply_setup(selection)).completed
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Hello")
        outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status.value == "completed", outcome.model_dump_json()
    assert calls == [True]


@pytest.mark.anyio
async def test_saved_key_connects_deferred_default_agent_and_first_native_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.model_accounts.api_keys import ApiKeyInput
    from a13n_harness_ui.prompts import DEFAULT_SYSTEM_PROMPT
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from pydantic import SecretStr
    from pydantic_ai.messages import ModelRequest, SystemPromptPart
    from pydantic_ai.models.function import FunctionModel

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "codex").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("GROK_AUTH_PATH", str(tmp_path / "grok.json"))
    monkeypatch.delenv("GROK_AUTH", raising=False)
    seen = []

    async def stream(messages, info):
        assert any(
            isinstance(p, SystemPromptPart) and p.content == DEFAULT_SYSTEM_PROMPT
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
        )
        assert "Keep my instructions." in info.instructions
        yield "Connected."

    async def infer(route, credential, *, base_url=None):
        seen.append(credential.api_key.get_secret_value())
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr("a13n_harness_ui.model_runtime.build_api_key_model", infer)
    path = tmp_path / "config" / "config.yaml"
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state")), configuration_path=path
    ) as app:
        deferred = _selection(tmp_path, model=None, default_agent="agent-default", instructions="Keep my instructions.")
        preview = await app.preview_setup(deferred)
        assert (await app.apply_setup(deferred)).completed
        await app.put_api_key(ApiKeyInput(credential_ref="key-test", key=SecretStr("first-secret")))
        selected = deferred.model_copy(update={"instructions": "", "connect_default": True})
        selected = SetupSelection.model_validate(
            {
                **selected.model_dump(),
                "model": {
                    "route": "openai:test",
                    "authentication": {"kind": "api_key", "credential_ref": "key-test"},
                },
            }
        )
        preview = await app.preview_setup(selected)
        assert "first-secret" not in preview.model_dump_json()
        assert "agents/default.yaml" in preview.files
        assert (await app.apply_setup(selected)).completed
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Hello")
        assert (await app.wait_root_operation(receipt.receipt_id)).status.value == "completed"
        await app.put_api_key(ApiKeyInput(credential_ref="key-test", key=SecretStr("rotated-secret")))
        next_thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=next_thread.thread_id, prompt="Hello again")
        assert (await app.wait_root_operation(receipt.receipt_id)).status.value == "completed"
        assert seen == ["first-secret", "rotated-secret"]
        await app.delete_api_key("key-test")
        next_thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=next_thread.thread_id, prompt="Missing key")
        assert (await app.wait_root_operation(receipt.receipt_id)).status.value != "completed"
        assert seen == ["first-secret", "rotated-secret"]
    loaded = await load_harness_ui_configuration(path)
    assert loaded.agents["agent-default"].instructions == "Keep my instructions."
    for file in path.parent.rglob("*.yaml"):
        assert "secret" not in file.read_text()


@pytest.mark.anyio
async def test_changed_api_key_model_gets_new_resource_without_rewriting_shared_model(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    first = _selection(
        tmp_path,
        default_agent="agent-api-key",
        connect_default=True,
        model={"route": "openai:first", "authentication": {"kind": "api_key", "env": "MY_KEY"}},
    )
    preview = await preview_setup(path, first, validate_candidate=_validate())
    assert (await publish_setup(path, first, validate_candidate=_validate())).completed
    old_model = (tmp_path / "models" / "api-key.yaml").read_bytes()
    second = SetupSelection.model_validate(
        {
            **first.model_dump(),
            "model": {"route": "openai:second", "authentication": {"kind": "api_key", "env": "MY_KEY"}},
        }
    )
    preview = await preview_setup(path, second, validate_candidate=_validate())
    assert "models/api-key.yaml" not in preview.files
    assert (await publish_setup(path, second, validate_candidate=_validate())).completed
    assert (tmp_path / "models" / "api-key.yaml").read_bytes() == old_model
    loaded = await load_harness_ui_configuration(path)
    assert loaded.agents["agent-api-key"].model != "model-api-key"
    # A content-derived name is not authority: users can edit the generated resource.
    generated = next((tmp_path / "models").glob("api-key-*.yaml"))
    edited = generated.read_text().replace("MY_KEY", "DIFFERENT_ACCOUNT_KEY")
    generated.write_text(edited)
    with pytest.raises(ConfigurationError, match="no longer matches"):
        await preview_setup(path, second, validate_candidate=_validate())
    assert generated.read_text() == edited


@pytest.mark.anyio
async def test_sandbox_adapter_selects_only_the_session_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace
    from unittest.mock import Mock

    from a13n_harness.providers.environment.local_envd.provider import LOCAL_ENVD
    from a13n_harness_ui.composition.models import ResolvedEnvironmentProfile
    from a13n_harness_ui.extensions import environment_adapters as adapters

    create = Mock()
    # Definitions are immutable: observe construction through a replaced definition.
    provider = replace(LOCAL_ENVD, construct=create)
    profile = ResolvedEnvironmentProfile(
        profile_id="environment-sandbox",
        behavior_digest="a" * 64,
        provider_key=adapters.LOCAL_ENVD_PROVIDER_KEY,
        adapter_key=adapters.LOCAL_ENVD_ADAPTER_KEY,
    )
    await adapters.LocalEnvdProjectAdapter().bind(
        profile=profile, root=tmp_path, state=None, provider=provider, runtime=None
    )
    configuration = create.call_args.kwargs["configuration"]
    assert configuration.working_directory == tmp_path.as_posix()
    assert configuration.egress is None and configuration.expected_boundary is None
    assert set(configuration.model_dump()) == {"working_directory", "required_methods", "egress", "expected_boundary"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider,model", [("codex", "gpt-6-astra"), ("grok", "grok-4.5"), ("grok", "grok-4.20-0309-reasoning")]
)
async def test_add_agent_preserves_existing_files_defaults_and_retry_identity(
    tmp_path: Path, provider: str, model: str
) -> None:
    path = tmp_path / "config.yaml"
    initial = _selection(tmp_path)
    preview = await preview_setup(path, initial, validate_candidate=_validate())
    assert (await publish_setup(path, initial, validate_candidate=_validate())).completed
    baseline = {p: p.read_bytes() for p in tmp_path.rglob("*.yaml")}
    added = _selection(
        tmp_path,
        model=prepare_model(
            ModelRecipeRequest(connection="codex" if provider == "codex" else "grok-subscription", model_id=model)
        ),
        new_agent_id="agent-second",
        new_agent_name="Second agent",
    )
    preview = await preview_setup(path, added, validate_candidate=_validate())
    assert preview.project_paths == ()
    assert path.name not in preview.files
    assert not any(name.startswith("projects/") for name in preview.files)
    assert (await publish_setup(path, added, validate_candidate=_validate())).completed
    source = await load_harness_ui_configuration(path)
    assert source.agents["agent-second"].model == "model-agent-second"
    assert source.models["model-agent-second"].route.endswith(":" + model)
    assert source.document.defaults.agent == "agent-codex"
    assert all(p.read_bytes() == content for p, content in baseline.items())
    again = await preview_setup(path, added, validate_candidate=_validate())
    assert not again.files
    changed = added.model_copy(update={"new_agent_name": "An accidental collision"})
    with pytest.raises(ConfigurationError, match="already in use"):
        await preview_setup(path, changed, validate_candidate=_validate())


@pytest.mark.anyio
async def test_setup_new_subscription_model_does_not_rewrite_shared_model(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    initial = _selection(tmp_path)
    await preview_setup(path, initial, validate_candidate=_validate())
    assert (await publish_setup(path, initial, validate_candidate=_validate())).completed
    model = (tmp_path / "models/codex.yaml").read_bytes()
    changed = initial.model_copy(
        update={
            "model": prepare_model(ModelRecipeRequest(connection="codex", model_id="gpt-6-astra")),
            "connect_default": True,
        }
    )
    await preview_setup(path, changed, validate_candidate=_validate())
    assert (await publish_setup(path, changed, validate_candidate=_validate())).completed
    source = await load_harness_ui_configuration(path)
    assert source.models[source.agents["agent-codex"].model].route == "openai-codex:gpt-6-astra"
    assert (tmp_path / "models/codex.yaml").read_bytes() == model


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", [True, False])
async def test_api_key_setup_publishes_root_shell_review_not_agent_capabilities(tmp_path: Path, enabled: bool) -> None:
    path = tmp_path / "config.yaml"
    selection = _selection(
        tmp_path,
        default_agent="agent-api-key",
        shell_review=enabled,
        model={"route": "openai:gpt-5", "authentication": {"kind": "api_key", "env": "TEST_KEY"}},
    )
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    shortcut = yaml.safe_load(preview.files[path.name])["security"]["shell_review"]
    assert shortcut["enable"] is enabled
    assert shortcut["risk_threshold"] == "extra_high"
    if enabled:
        assert shortcut["model"] == "model-api-key"
    agent = yaml.safe_load(preview.files["agents/api-key.yaml"])
    assert not any(c["capability"] in {"ToolPermissionsCapability"} for c in agent["capabilities"])
    assert set(p for p in preview.files if p.startswith("models/")) == {"models/api-key.yaml"}
    assert (await publish_setup(path, selection, validate_candidate=_validate())).completed


@pytest.mark.anyio
@pytest.mark.parametrize(
    "shortcut", [{"enable": False}, {"enable": True, "risk_threshold": "high", "model": "model-codex"}]
)
async def test_setup_and_add_agent_preserve_authored_root_shortcut(tmp_path: Path, shortcut: dict[str, object]) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"schema_version": "1", "security": {"shell_review": shortcut}}))
    selection = _selection(tmp_path)
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    assert yaml.safe_load(preview.files[path.name])["security"]["shell_review"] == shortcut
    assert "models/codex-review.yaml" not in preview.files
    assert (await publish_setup(path, selection, validate_candidate=_validate())).completed
    baseline = path.read_bytes()
    addition = _selection(
        tmp_path, model=None, new_agent_id="agent-second", new_agent_name="Second", existing_model_id="model-codex"
    )
    preview = await preview_setup(path, addition, validate_candidate=_validate())
    assert set(preview.files) == {"agents/second.yaml"}
    assert (await publish_setup(path, addition, validate_candidate=_validate())).completed
    assert path.read_bytes() == baseline


@pytest.mark.anyio
async def test_add_agent_initializes_absent_root_shortcut_without_mutating_existing_agent(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    selection = _selection(tmp_path)
    assert (await publish_setup(path, selection, validate_candidate=_validate())).completed
    root = yaml.safe_load(path.read_text())
    del root["security"]
    path.write_text(yaml.safe_dump(root))
    agent = tmp_path / "agents" / "codex.yaml"
    baseline = agent.read_bytes()
    addition = _selection(
        tmp_path, model=None, new_agent_id="agent-second", new_agent_name="Second", existing_model_id="model-codex"
    )
    preview = await preview_setup(path, addition, validate_candidate=_validate())
    assert yaml.safe_load(preview.files[path.name])["security"]["shell_review"]["model"] == "model-codex-review"
    assert (await publish_setup(path, addition, validate_candidate=_validate())).completed
    assert agent.read_bytes() == baseline
