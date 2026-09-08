from pathlib import Path

import pytest
from a13n_harness_ui.composition import AgentCompositionResolver
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.configuration.setup import SetupSelection, preview_setup, publish_setup
from a13n_harness_ui.errors import ConfigurationError
from a13n_harness_ui.extensions import HarnessUiExtensionCatalog


def _selection(tmp_path: Path, **changes: object) -> SetupSelection:
    return SetupSelection.model_validate(
        {
            "providers": ("codex", "grok"),
            "default_agent": "agent-codex",
            "project_path": str(tmp_path),
            "environment_profile": "environment-native",
            **changes,
        }
    )


def _validate():
    return AgentCompositionResolver(HarnessUiExtensionCatalog()).validate_generation


@pytest.mark.anyio
async def test_setup_previews_without_publication_and_seeds_both_providers(tmp_path: Path) -> None:
    path = tmp_path / "config" / "config.yaml"
    selection = _selection(tmp_path)
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    assert not path.parent.exists()
    assert "gpt-5.6-luna" in preview.files["models/codex-review.yaml"]
    result = await publish_setup(
        path, selection, expected_generation=preview.generation, validate_candidate=_validate()
    )
    assert result.completed
    assert result.published_paths[-1] == path.name
    source = await load_harness_ui_configuration(path)
    assert source.document.defaults.agent == "agent-codex"
    assert len(source.agents) == 2


@pytest.mark.anyio
async def test_setup_preserves_edited_resources_and_root_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    selection = _selection(tmp_path)
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    assert (
        await publish_setup(path, selection, expected_generation=preview.generation, validate_candidate=_validate())
    ).completed
    agent = tmp_path / "agents" / "codex.yaml"
    original = agent.read_text().replace("Codex coding", "My edited agent")
    agent.write_text(original)
    path.write_text(path.read_text() + "process:\n  pricing_auto_update: false\n")
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    assert "agents/codex.yaml" not in preview.files
    assert (
        await publish_setup(path, selection, expected_generation=preview.generation, validate_candidate=_validate())
    ).completed
    assert agent.read_text() == original
    assert "pricing_auto_update: false" in path.read_text()


@pytest.mark.anyio
async def test_setup_rejects_stale_preview_without_writes(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    selection = _selection(tmp_path)
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    path.write_text('schema_version: "2"\n')
    with pytest.raises(ConfigurationError, match="stale"):
        await publish_setup(path, selection, expected_generation=preview.generation, validate_candidate=_validate())
    assert not (tmp_path / "models").exists()


@pytest.mark.anyio
async def test_setup_partial_publication_is_retryable_without_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness_ui.configuration import setup

    path = tmp_path / "config.yaml"
    selection = _selection(tmp_path)
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    publish = setup._publish_content
    calls = 0

    def fail_second(path: Path, content: bytes, expected: str | None) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("test failure")
        publish(path, content, expected)

    monkeypatch.setattr(setup, "_publish_content", fail_second)
    result = await publish_setup(
        path, selection, expected_generation=preview.generation, validate_candidate=_validate()
    )
    assert not result.completed
    assert len(result.published_paths) == 1
    assert not path.exists()
    retained = (tmp_path / result.published_paths[0]).read_bytes()
    monkeypatch.setattr(setup, "_publish_content", publish)
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    assert (
        await publish_setup(path, selection, expected_generation=preview.generation, validate_candidate=_validate())
    ).completed
    assert (tmp_path / result.published_paths[0]).read_bytes() == retained


@pytest.mark.anyio
async def test_setup_rejects_occupied_destination_for_another_resource(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "codex.yaml").write_text(
        'schema_version: "1"\nkind: agent\nid: agent-unrelated\nname: Unrelated\nmodel: model-grok\n'
    )
    with pytest.raises(ConfigurationError, match="belongs to another resource"):
        await preview_setup(path, _selection(tmp_path, default_agent="agent-grok"), validate_candidate=_validate())
    assert not path.exists()


@pytest.mark.anyio
async def test_setup_reports_final_generation_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from a13n_harness_ui.configuration import setup

    path = tmp_path / "config.yaml"
    selection = _selection(tmp_path)
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    load = setup.load_harness_ui_configuration

    async def concurrent_load(selected: Path, **kwargs):
        if selected == path:
            path.write_text(path.read_text().replace("agent-codex", "agent-grok"))
        return await load(selected, **kwargs)

    monkeypatch.setattr(setup, "load_harness_ui_configuration", concurrent_load)
    result = await publish_setup(
        path, selection, expected_generation=preview.generation, validate_candidate=_validate()
    )
    assert not result.completed
    assert result.error_code == "configuration_mutation_conflict"
    assert "agent-grok" in path.read_text()


@pytest.mark.anyio
async def test_setup_preserves_root_save_racing_with_detachment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness_ui.configuration import setup

    path = tmp_path / "config.yaml"
    path.write_text('schema_version: "2"\n')
    selection = _selection(tmp_path)
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    rename = setup.os.rename
    external = 'schema_version: "2"\nprocess: {log_level: DEBUG}\n'

    def racing_rename(source, target):
        if source == path:
            path.write_text(external)
        rename(source, target)

    monkeypatch.setattr(setup.os, "rename", racing_rename)
    result = await publish_setup(
        path, selection, expected_generation=preview.generation, validate_candidate=_validate()
    )
    assert not result.completed
    assert result.error_code == "configuration_mutation_conflict"
    assert path.read_text() == external
    assert not list(tmp_path.glob(".a13n-harness-ui-setup-recovery-*"))


@pytest.mark.anyio
async def test_setup_preserves_new_root_during_detached_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness_ui.configuration import setup

    path = tmp_path / "config.yaml"
    original = 'schema_version: "2"\n'
    path.write_text(original)
    selection = _selection(tmp_path)
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    publish = setup._publish_content
    external = 'schema_version: "2"\nprocess: {log_level: DEBUG}\n'

    def racing_publish(target, content, expected):
        if target == path:
            path.write_text(external)
        publish(target, content, expected)

    monkeypatch.setattr(setup, "_publish_content", racing_publish)
    result = await publish_setup(
        path, selection, expected_generation=preview.generation, validate_candidate=_validate()
    )
    assert not result.completed
    assert path.read_text() == external
    retained = list(tmp_path.glob(".a13n-harness-ui-setup-recovery-*/config.yaml"))
    assert len(retained) == 1
    assert retained[0].read_text() == original
    with pytest.raises(ConfigurationError, match="interrupted setup"):
        await preview_setup(path, selection, validate_candidate=_validate())


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
    from a13n_environment import EnvironmentError
    from a13n_harness_ui import setup

    observed = []

    async def resolve() -> Path:
        return tmp_path / "envd"

    async def probe(executable, configuration):
        observed.append((executable, configuration.execution_network.value))
        raise EnvironmentError("probe failed", code="provider_unavailable")

    monkeypatch.setattr(setup, "local_sandbox_supported", lambda: True)
    monkeypatch.setattr(setup, "validate_local_envd_runtime", probe)
    result = await setup.preflight_environment("environment-sandbox", tmp_path, resolve_executable=resolve)
    assert not result.ready
    assert result.profile_id == "environment-sandbox"
    assert observed == [(tmp_path / "envd", "deny")]
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


def test_setup_recovery_syncs_replacement_before_unlinking_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness_ui.configuration import setup

    root = tmp_path / "config.yaml"
    root.write_text('schema_version: "2"\n')
    digest = setup._source_digest_or_none(root)
    assert digest is not None
    ordering: list[tuple[str, bool, bool]] = []

    def sync(directory: Path) -> None:
        retained = tuple(tmp_path.glob(".a13n-harness-ui-setup-recovery-*/config.yaml"))
        ordering.append(("root" if directory == tmp_path else "recovery", root.exists(), bool(retained)))

    def fail_publication(*args) -> None:
        assert ordering[:2] == [("recovery", False, True), ("root", False, True)]
        raise OSError("injected publication failure")

    monkeypatch.setattr(setup, "_fsync_directory", sync)
    monkeypatch.setattr(setup, "_publish_content", fail_publication)
    with pytest.raises(OSError, match="injected"):
        setup._publish_root_defaults(root, b"new root", digest)
    assert ordering[2:4] == [("root", True, True), ("recovery", True, False)]
    assert root.read_text() == 'schema_version: "2"\n'


@pytest.mark.anyio
@pytest.mark.parametrize("review_outcome", ["flagged", "error"])
async def test_codex_setup_routes_shell_review_to_luna_and_requests_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    review_outcome: str,
) -> None:
    import json

    import a13n_harness.model_auth as runtime
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    # Both native Model factories are replaced, not the App composition or
    # shell-review pipeline. No compatible account credential is read by a model.
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "codex").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    marker = tmp_path / "must-not-exist"
    reviewed: list[str | bool | None] = []
    resolved: list[str] = []

    async def main_stream(messages, info):
        assert info.model_request_parameters.thinking == "high"
        yield {
            0: DeltaToolCall(
                name="shell_exec",
                json_args=json.dumps({"command": f"echo denied > {marker}"}),
                tool_call_id="review-call",
            )
        }

    async def review(messages, info):
        reviewed.append(info.model_request_parameters.thinking)
        assert not info.function_tools
        if review_outcome == "error":
            raise RuntimeError("Synthetic reviewer unavailable")
        yield '{"risk":"extra_high","reason":"Requires user review"}'

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
    selection = _selection(tmp_path, providers=("codex",))
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    assert (
        await publish_setup(path, selection, expected_generation=preview.generation, validate_candidate=_validate())
    ).completed
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state")), configuration_path=path
    ) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Try a reviewed command")
        outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status.value == "suspended", (outcome.model_dump_json(), resolved, reviewed)
        batch = await app.thread_decisions(thread_id=thread.thread_id)
        assert batch is not None and len(batch.requests) == 1
        assert batch.requests[0].kind == "approval"
    assert set(resolved) == {"gpt-5.6-luna", "gpt-5.6-sol"}
    assert reviewed == ["low"]
    assert not marker.exists()


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
    monkeypatch.setattr(setup, "validate_local_envd_runtime", probe)
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
    selection = _selection(tmp_path, providers=(), default_agent="agent-default")
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state")), configuration_path=path
    ) as app:
        assert (await app.setup_status()).needed
        preview = await app.preview_setup(selection)
        assert not path.exists()
        assert not any(name.startswith("models/") for name in preview.files)
        assert (await app.apply_setup(selection, expected_generation=preview.generation)).completed
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
async def test_api_key_setup_publishes_only_reference_and_additional_instructions(tmp_path: Path) -> None:
    selection = _selection(
        tmp_path,
        providers=(),
        default_agent="agent-api-key",
        api_key_model={"route": "openai:gpt-5", "authentication": {"kind": "api_key", "env": "MY_EXISTING_KEY"}},
        instructions="Answer briefly.",
    )
    path = tmp_path / "config.yaml"
    preview = await preview_setup(path, selection, validate_candidate=_validate())
    assert "MY_EXISTING_KEY" in preview.files["models/api-key.yaml"]
    assert "Answer briefly." in preview.files["agents/api-key.yaml"]
    assert "system_prompt" not in preview.files["agents/api-key.yaml"]
    assert (
        await publish_setup(path, selection, expected_generation=preview.generation, validate_candidate=_validate())
    ).completed
    source = await load_harness_ui_configuration(path)
    assert source.agents["agent-api-key"].model == "model-api-key"
    assert source.models["model-api-key"].authentication.env == "MY_EXISTING_KEY"


@pytest.mark.anyio
async def test_setup_run_delivers_base_and_additions_through_distinct_native_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_harness.model_auth as runtime
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
        selection = _selection(tmp_path, providers=("codex",), instructions="Use short answers.", shell_review=False)
        preview = await app.preview_setup(selection)
        assert (await app.apply_setup(selection, expected_generation=preview.generation)).completed
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

    def infer(route, *, provider_factory):
        provider = provider_factory("openai-responses")
        seen.append(provider.client.api_key)
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr("a13n_harness_ui.model_runtime.infer_model", infer)
    path = tmp_path / "config" / "config.yaml"
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state")), configuration_path=path
    ) as app:
        deferred = _selection(
            tmp_path, providers=(), default_agent="agent-default", instructions="Keep my instructions."
        )
        preview = await app.preview_setup(deferred)
        assert (await app.apply_setup(deferred, expected_generation=preview.generation)).completed
        await app.put_api_key(ApiKeyInput(credential_ref="key-test", key=SecretStr("first-secret")))
        selected = deferred.model_copy(update={"instructions": "", "connect_default": True})
        selected = SetupSelection.model_validate(
            {
                **selected.model_dump(),
                "api_key_model": {
                    "route": "openai:test",
                    "authentication": {"kind": "api_key", "credential_ref": "key-test"},
                },
            }
        )
        preview = await app.preview_setup(selected)
        assert "first-secret" not in preview.model_dump_json()
        assert "agents/default.yaml" in preview.files
        assert (await app.apply_setup(selected, expected_generation=preview.generation)).completed
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
        providers=(),
        default_agent="agent-api-key",
        connect_default=True,
        api_key_model={"route": "openai:first", "authentication": {"kind": "api_key", "env": "MY_KEY"}},
    )
    preview = await preview_setup(path, first, validate_candidate=_validate())
    assert (
        await publish_setup(path, first, expected_generation=preview.generation, validate_candidate=_validate())
    ).completed
    old_model = (tmp_path / "models" / "api-key.yaml").read_bytes()
    second = SetupSelection.model_validate(
        {
            **first.model_dump(),
            "api_key_model": {"route": "openai:second", "authentication": {"kind": "api_key", "env": "MY_KEY"}},
        }
    )
    preview = await preview_setup(path, second, validate_candidate=_validate())
    assert "models/api-key.yaml" not in preview.files
    assert (
        await publish_setup(path, second, expected_generation=preview.generation, validate_candidate=_validate())
    ).completed
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
async def test_windows_sandbox_shell_recipe_stays_in_eip_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    from a13n_environment import LocalEnvdEnvironmentProvider
    from a13n_harness_ui.composition.models import ResolvedEnvironmentProfile
    from a13n_harness_ui.extensions import environment_adapters as adapters

    provider = LocalEnvdEnvironmentProvider()
    create = Mock()
    monkeypatch.setattr(provider, "create_environment", create)
    monkeypatch.setattr(adapters.sys, "platform", "win32")
    monkeypatch.setattr(adapters, "_host_shell", lambda: tmp_path / "pwsh.exe")
    profile = ResolvedEnvironmentProfile(
        profile_id="environment-sandbox",
        behavior_digest="a" * 64,
        provider_key=adapters.LOCAL_ENVD_PROVIDER_KEY,
        provider_schema_version=next(iter(provider.configuration_versions)),
        adapter_key=adapters.LOCAL_ENVD_ADAPTER_KEY,
    )
    await adapters.LocalEnvdProjectAdapter().bind(
        profile=profile, root=tmp_path, state=None, provider=provider, runtime=None
    )
    configuration = create.call_args.kwargs["configuration"]
    shell = configuration.shell_profiles[0]
    assert shell.fixed_arguments == ("-NoLogo", "-NoProfile", "-NonInteractive", "-OutputFormat", "Text", "-Command")
    assert not shell.allow_login
    assert configuration.execution_network.value == "deny"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider,model", [("codex", "gpt-6-astra"), ("grok", "grok-4.5"), ("grok", "grok-4.20-0309-reasoning")]
)
async def test_add_agent_preserves_existing_files_defaults_and_retry_identity(
    tmp_path: Path, provider: str, model: str
) -> None:
    path = tmp_path / "config.yaml"
    initial = _selection(tmp_path, providers=("codex",))
    preview = await preview_setup(path, initial, validate_candidate=_validate())
    assert (
        await publish_setup(path, initial, expected_generation=preview.generation, validate_candidate=_validate())
    ).completed
    baseline = {p: p.read_bytes() for p in tmp_path.rglob("*.yaml")}
    added = _selection(
        tmp_path,
        providers=(provider,),
        new_agent_id="agent-second",
        new_agent_name="Second agent",
        **{f"{provider}_model": model},
    )
    preview = await preview_setup(path, added, validate_candidate=_validate())
    assert preview.project_paths == ()
    assert path.name not in preview.files
    assert not any(name.startswith("projects/") for name in preview.files)
    assert (
        await publish_setup(path, added, expected_generation=preview.generation, validate_candidate=_validate())
    ).completed
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
    initial = _selection(tmp_path, providers=("codex",))
    preview = await preview_setup(path, initial, validate_candidate=_validate())
    assert (
        await publish_setup(path, initial, expected_generation=preview.generation, validate_candidate=_validate())
    ).completed
    model = (tmp_path / "models/codex.yaml").read_bytes()
    changed = initial.model_copy(update={"codex_model": "gpt-6-astra", "connect_default": True})
    preview = await preview_setup(path, changed, validate_candidate=_validate())
    assert (
        await publish_setup(path, changed, expected_generation=preview.generation, validate_candidate=_validate())
    ).completed
    source = await load_harness_ui_configuration(path)
    assert source.models[source.agents["agent-codex"].model].route == "openai-codex:gpt-6-astra"
    assert (tmp_path / "models/codex.yaml").read_bytes() == model
