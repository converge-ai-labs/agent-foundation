"""Verify development seeding with real configuration loading and isolated user state."""

from __future__ import annotations

import importlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from a13n_harness_ui.model_accounts.api_keys import ApiKeyStore
from a13n_harness_ui.settings_loader import load_harness_ui_settings

launcher = importlib.import_module("dev.harness-ui.cli")
cli_module = importlib.import_module("a13n_harness_ui.cli")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def development(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "user home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("A13N_HARNESS_UI_DATA_ROOT", raising=False)
    root = tmp_path / "workspace with spaces"
    root.mkdir()
    monkeypatch.setattr(launcher, "ROOT", root)
    return root / "var/harness-ui/a13n-harness-ui.yaml"


def write_source() -> Path:
    root = launcher.default_harness_ui_settings_path()
    files = {
        root.name: 'schema_version: "1"\ndefaults:\n  agent: agent-existing\n',
        "models/existing.yaml": (
            'schema_version: "1"\nkind: model\nid: model-existing\nname: Existing Model\n'
            "route: openai-chat:local-scripted\nauthentication:\n  kind: api_key\n  credential_ref: key-existing\n"
        ),
        "agents/existing.yaml": (
            'schema_version: "1"\nkind: agent\nid: agent-existing\nname: Existing Agent\nmodel: model-existing\n'
        ),
        "AGENTS.md": "Keep the existing user guidance.\n",
        "data/auth.json": json.dumps({"version": 1, "keys": {"key-existing": "fictional-api-key"}}),
    }
    for name, content in files.items():
        path = root.parent / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def invoke(monkeypatch: pytest.MonkeyPatch, *arguments: str):
    requests = []
    monkeypatch.setattr(sys, "argv", ["dev.harness-ui.cli", *arguments])
    monkeypatch.setattr(cli_module, "_execute", requests.append)
    with pytest.raises(SystemExit) as exc:
        launcher.main()
    return exc.value.code, requests


@pytest.mark.anyio
async def test_first_launch_reuses_models_keys_and_guidance_but_not_history(
    development: Path, monkeypatch, capsys
) -> None:
    source = write_source()
    ignored = (
        "data/harness-ui.sqlite3",
        "data/objects/history.json",
        "data/content-plugins/plugin/README.md",
        "logs/debug.log",
    )
    for name in ignored:
        path = source.parent / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private state that must not be copied")
    before = {path.relative_to(source.parent): path.read_bytes() for path in source.parent.rglob("*") if path.is_file()}

    code, requests = invoke(monkeypatch, "config", "validate")

    assert code == 0
    assert requests[0].config_path == development
    assert requests[0].data_root == development.parent / "data"
    assert requests[0].no_update_check
    loaded = await load_harness_ui_settings(development)
    assert loaded.candidate_error is None
    assert loaded.configuration.document.defaults.agent == "agent-existing"
    assert loaded.configuration.agents["agent-existing"].model == "model-existing"
    assert loaded.configuration.models["model-existing"].authentication.credential_ref == "key-existing"
    assert await ApiKeyStore(development.parent / "data/auth.json").load("key-existing") == "fictional-api-key"
    assert (development.parent / "AGENTS.md").read_bytes() == (source.parent / "AGENTS.md").read_bytes()
    for name in ignored:
        assert not (development.parent / name).exists()
    assert before == {
        path.relative_to(source.parent): path.read_bytes() for path in source.parent.rglob("*") if path.is_file()
    }
    if os.name != "nt":
        assert (development.parent / "data/auth.json").stat().st_mode & 0o777 == 0o600
    assert "fictional-api-key" not in capsys.readouterr().err


def test_only_immediate_supported_resource_files_are_copied(development: Path) -> None:
    source = write_source()
    included = (
        "mcp/servers.json",
        "mcp/other.yaml",
        "extensions/custom.yaml",
        "projects/local.yaml",
        "subagents/review.md",
    )
    excluded = ("models/ignored.YAML", "models/nested/ignored.yaml", "subagents/README.md", "models/backup.yaml.bak")
    for name in (*included, *excluded):
        path = source.parent / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("preserve exact bytes, even if the configuration needs repair\n")
    launcher.initialize_configuration(development, development.parent / "data")
    for name in included:
        assert (development.parent / name).read_bytes() == (source.parent / name).read_bytes()
    for name in excluded:
        assert not (development.parent / name).exists()


def test_subsequent_launch_preserves_every_existing_file(development: Path) -> None:
    source = write_source()
    launcher.initialize_configuration(development, development.parent / "data")
    (development.parent / "models/existing.yaml").write_text("development edits")
    (development.parent / "data/auth.json").write_text("development keys")
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in development.parent.rglob("*") if path.is_file()
    }
    source.write_text("new user config")
    (source.parent / "models/new.yaml").write_text("new user model")
    launcher.initialize_configuration(development, development.parent / "data")
    assert before == {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in development.parent.rglob("*") if path.is_file()
    }
    assert not (development.parent / "models/new.yaml").exists()


def test_preexisting_resources_and_keys_survive_first_seed(development: Path) -> None:
    write_source()
    for name in ("models/existing.yaml", "data/auth.json", "data/harness-ui.sqlite3"):
        path = development.parent / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("keep this existing file")
    launcher.initialize_configuration(development, development.parent / "data")
    assert development.is_file()
    for name in ("models/existing.yaml", "data/auth.json", "data/harness-ui.sqlite3"):
        assert (development.parent / name).read_text() == "keep this existing file"


def test_api_keys_follow_source_environment_and_explicit_destination(development: Path, monkeypatch) -> None:
    source = write_source()
    source_data = source.parent / "custom source data"
    source_data.mkdir()
    (source_data / "auth.json").write_text("selected key store")
    monkeypatch.setenv("A13N_HARNESS_UI_DATA_ROOT", str(source_data))
    target_data = development.parent / "custom target data"
    code, requests = invoke(monkeypatch, f"--data-root={target_data}", "config", "validate")
    assert code == 0
    assert requests[0].data_root == target_data
    assert (target_data / "auth.json").read_text() == "selected key store"
    assert not (development.parent / "data").exists()


def test_environment_does_not_redirect_development_data(development: Path, monkeypatch) -> None:
    monkeypatch.setenv("A13N_HARNESS_UI_DATA_ROOT", str(development.parent / "wrong data"))
    code, requests = invoke(monkeypatch, "config", "path")
    assert code == 0
    assert requests[0].data_root == development.parent / "data"
    assert not (development.parent / "wrong data").exists()


@pytest.mark.parametrize("style", ["separate", "equals", "repeated"])
def test_explicit_config_skips_initialization_even_when_missing(development: Path, monkeypatch, style: str) -> None:
    write_source()
    custom = development.parent / "custom config.yaml"
    arguments = ["--config", str(custom)] if style != "equals" else [f"--config={custom}"]
    if style == "repeated":
        arguments = ["--config", "ignored.yaml", *arguments]
    code, requests = invoke(monkeypatch, *arguments, "config", "validate")
    assert code == 0
    assert requests[0].config_path == custom
    assert requests[0].data_root == development.parent / "data"
    assert not development.parent.exists()


@pytest.mark.parametrize("arguments", [("--help",), ("--version",), ("webui", "--help"), ("config", "validate", "-h")])
def test_help_and_version_do_not_copy_private_configuration(development: Path, monkeypatch, arguments) -> None:
    write_source()
    code, requests = invoke(monkeypatch, *arguments)
    assert code == 0
    assert requests == []
    assert not development.parent.exists()


def test_failed_copy_does_not_publish_root_and_can_be_retried(development: Path, monkeypatch) -> None:
    write_source()
    copy_missing = launcher._copy_missing

    def fail_on_agent(source: Path, target: Path) -> None:
        if source.parent.name == "agents":
            raise OSError("test copy failure")
        copy_missing(source, target)

    monkeypatch.setattr(launcher, "_copy_missing", fail_on_agent)
    code, requests = invoke(monkeypatch, "config", "validate")
    assert code == 2
    assert requests == []
    assert not development.exists()
    monkeypatch.setattr(launcher, "_copy_missing", copy_missing)
    launcher.initialize_configuration(development, development.parent / "data")
    assert development.is_file()
    assert (development.parent / "agents/existing.yaml").is_file()


def test_concurrent_first_launches_do_not_overwrite_each_other(development: Path) -> None:
    source = write_source()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(launcher.initialize_configuration, development, development.parent / "data") for _ in range(2)
        ]
        for future in futures:
            future.result(timeout=20)
    assert development.read_bytes() == source.read_bytes()
    assert not list(development.parent.rglob(".initialize-*"))


@pytest.mark.parametrize("location", ["source", "destination"])
def test_resource_directory_symlinks_are_not_followed(development: Path, location: str) -> None:
    source = write_source()
    outside = development.parents[2] / "outside"
    outside.mkdir()
    root = source if location == "source" else development
    root.parent.mkdir(parents=True, exist_ok=True)
    (root.parent / "extensions").symlink_to(outside, target_is_directory=True)
    if location == "destination":
        (source.parent / "extensions").mkdir()
        (source.parent / "extensions/custom.yaml").write_text("must not write through the link")
    with pytest.raises(OSError, match="symlink"):
        launcher.initialize_configuration(development, development.parent / "data")
    assert list(outside.iterdir()) == []
    assert not development.exists()


@pytest.mark.anyio
async def test_user_without_configuration_can_enter_setup_through_development_launcher(
    development: Path, monkeypatch
) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.configuration.setup import SetupSelection

    home = Path.home()
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("GROK_HOME", str(home / ".grok"))
    monkeypatch.delenv("GROK_AUTH_PATH", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    code, requests = invoke(monkeypatch, "setup")
    assert code == 0
    source = await load_harness_ui_settings(requests[0].config_path, data_root=requests[0].data_root)
    assert source.candidate_error is None
    assert source.configuration is not None
    settings = source.settings.model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(
        settings, configuration_path=source.path, configuration_error=source.candidate_error
    ) as app:
        status = await app.setup_status()
        assert status.needed
        assert status.diagnostic is None
        selection = SetupSelection(
            providers=(), default_agent="agent-default", environment_profile="environment-native"
        )
        assert (await app.apply_setup(selection)).completed
        assert not (await app.setup_status()).needed
    assert not launcher.default_harness_ui_settings_path().exists()


@pytest.mark.anyio
async def test_copied_model_and_key_complete_a_real_app_turn(development: Path, monkeypatch) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.surfaces import RootOperationStatus

    from dev.service.model import model_process

    home = Path.home()
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("GROK_HOME", str(home / ".grok"))
    monkeypatch.delenv("GROK_AUTH_PATH", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    source = write_source()
    with model_process(port=0) as url:
        model_path = source.parent / "models/existing.yaml"
        model_path.write_text(model_path.read_text() + f"model_configuration:\n  base_url: {url}\n")
        launcher.initialize_configuration(development, development.parent / "data")
        loaded = await load_harness_ui_settings(development)
        settings = loaded.settings.model_copy(update={"pricing_auto_update": False})
        async with open_harness_ui_app(
            settings, configuration_path=development, configuration_error=loaded.candidate_error
        ) as app:
            assert not (await app.setup_status()).needed
            thread = await app.create_thread()
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Hello from the development copy.")
            operation = await app.wait_root_operation(receipt.receipt_id, timeout_seconds=30)
            assert operation.status is RootOperationStatus.completed, operation
            assert operation.outcome is not None
            assert operation.outcome.execution.output
    assert not (source.parent / "data/harness-ui.sqlite3").exists()
