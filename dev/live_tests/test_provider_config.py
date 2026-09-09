"""Offline configuration partitions and cleanup after partial/failed integration."""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from . import provider_config
from .provider_config import ProviderSettings, load_provider_settings


def test_missing_default_and_blank_example_preserve_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_TEST_PROVIDERS_CONFIG", raising=False)
    monkeypatch.setattr(provider_config, "DEFAULT_PATH", tmp_path / "missing.toml")
    assert load_provider_settings() == ProviderSettings()
    assert load_provider_settings(Path(__file__).with_name("providers.example.toml")) == ProviderSettings()


def test_override_and_independent_sections_keep_secrets_private(tmp_path, monkeypatch):
    path = tmp_path / "settings.toml"
    path.write_text('[environment]\ntype="a13n.e2b"\napi_key="sample-secret"\n[model]\nprovider=""\napi_key=""\n')
    monkeypatch.setenv("LIVE_TEST_PROVIDERS_CONFIG", str(path))
    config = load_provider_settings()
    assert config.environment.type == "a13n.e2b" and config.environment.template == "base"
    assert config.environment.api_key.get_secret_value() == "sample-secret"
    assert config.connector is None and config.model is None
    assert "sample-secret" not in repr(config) + config.model_dump_json()


@pytest.mark.parametrize(
    "source",
    [
        '[environment]\napi_key="sample-secret"',
        '[environment]\ntype="unsupported"\napi_key="sample-secret"',
        '[connector]\nprovider="composio"',
        '[connector]\nprovider="openconnector"\ncatalog_api_key="sample-secret"',
        '[connector]\nprovider="openconnector"\nproject_api_key="sample-secret"',
        '[connector]\nprovider="openconnector"\nproject_api_key="sample-secret"\ncatalog_api_key="key"\nservices=[]',
        '[connector]\nprovider="composio"\napi_key="key"\nproject_api_key="sample-secret"',
        '[connector]\nprovider="unknown"\napi_key="sample-secret"',
        '[model]\nprovider="openrouter"\napi_key="sample-secret"',
        '[model]\nprovider="openai_compatible"\napi_key="sample-secret"\nmodel="model"',
        '[model]\nprovider="openai_compatible"\napi_key="sample-secret"\nmodel="model"\nbase_url="https://sample-secret@example.com"',
        '[environment]\napi_key="sample-secret',
        'unknown="sample-secret"',
    ],
)
def test_invalid_config_fails_without_echoing_input(tmp_path, source):
    path = tmp_path / "settings.toml"
    path.write_text(source)
    with pytest.raises(ValueError, match="Provider configuration") as caught:
        load_provider_settings(path)
    assert "sample-secret" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_explicit_missing_file_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_TEST_PROVIDERS_CONFIG", str(tmp_path / "missing.toml"))
    with pytest.raises(ValueError, match="Provider configuration"):
        load_provider_settings()


@pytest.mark.parametrize("provider", ["openrouter", "openai_compatible"])
def test_enabled_model_and_connector_sections(tmp_path, provider):
    path = tmp_path / "settings.toml"
    endpoint = 'base_url="https://models.example/v1"' if provider == "openai_compatible" else ""
    path.write_text(
        '[connector]\nprovider="composio"\napi_key="connector-key"\n'
        f'[model]\nprovider="{provider}"\napi_key="model-key"\nmodel="upstream/model"\n{endpoint}\n'
    )
    config = load_provider_settings(path)
    assert config.connector.toolkits == ["github"]
    assert config.model.model == "upstream/model" and config.model.provider == provider
    assert config.environment is None


def test_openconnector_configuration_uses_two_private_keys(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        '[connector]\nprovider="openconnector"\nproject_api_key="project-secret"\ncatalog_api_key="catalog-secret"\n'
    )
    config = load_provider_settings(path)
    assert config.connector.services == ["slack"]
    assert config.connector.project_api_key.get_secret_value() == "project-secret"
    assert config.connector.catalog_api_key.get_secret_value() == "catalog-secret"
    for secret in ("project-secret", "catalog-secret"):
        assert secret not in repr(config) + config.model_dump_json()


@pytest.mark.anyio
@pytest.mark.parametrize("section,flag", [("model", ""), ("slack", ""), ("model", "--live-slack")])
async def test_offline_fixture_does_not_read_private_configuration(monkeypatch, section, flag):
    from .conftest import configured_provider

    def unexpected_read():
        raise AssertionError("Offline collection read private configuration")

    monkeypatch.setattr(provider_config, "load_provider_settings", unexpected_read)
    request = SimpleNamespace(config=SimpleNamespace(getoption=lambda option: option == flag), param=section)
    fixture = configured_provider.__wrapped__(request)
    with pytest.raises(pytest.skip.Exception):
        await anext(fixture)


@pytest.mark.anyio
async def test_provider_opt_in_does_not_start_interactive_slack(monkeypatch):
    from .conftest import configured_provider

    def unexpected_read():
        raise AssertionError("Noninteractive run read Slack configuration")

    monkeypatch.setattr(provider_config, "load_provider_settings", unexpected_read)
    request = SimpleNamespace(
        config=SimpleNamespace(getoption=lambda option: option == "--live-providers"), param="slack"
    )
    with pytest.raises(pytest.skip.Exception, match="interactive Slack OAuth"):
        await anext(configured_provider.__wrapped__(request))


@pytest.mark.anyio
async def test_cleanup_attempts_every_owned_environment_even_after_run_failure():
    from .real_providers import cleanup_provider_lab

    live = SimpleNamespace(
        cleanup=AsyncMock(side_effect=RuntimeError("Run failure")),
        collection=AsyncMock(
            return_value=[{"id": "env_first", "status": "running"}, {"id": "env_second", "status": "unprepared"}]
        ),
    )
    journey = SimpleNamespace(
        live=live,
        base="/api/v1/workspaces/ws_owned",
        environment_command=AsyncMock(side_effect=[RuntimeError("first failed"), {"status": "deleted"}]),
    )
    with pytest.raises(AssertionError, match="env_first"):
        await cleanup_provider_lab(journey)
    assert journey.environment_command.await_args_list[1].args == ("env_second", "delete")
    live.collection.assert_awaited_once_with(journey.base + "/environments")


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["provision", "test"])
async def test_provider_cleanup_precedes_lab_teardown_after_failure(monkeypatch, failure):
    from . import real_providers

    events = []

    @asynccontextmanager
    async def lab(**kwargs):
        try:
            yield SimpleNamespace(client=SimpleNamespace(config={"workspace_id": "ws_owned"}))
        finally:
            events.append("lab removed")

    async def provision(*args):
        if failure == "provision":
            raise RuntimeError("provision failed")
        return {"id": "provider"}

    async def cleanup(*args):
        events.append("remote cleanup")

    monkeypatch.setattr(real_providers, "open_lab", lab)
    monkeypatch.setattr(real_providers, "provision_provider", provision)
    monkeypatch.setattr(real_providers, "cleanup_provider_lab", cleanup)
    with pytest.raises(RuntimeError):
        async with real_providers.configured_provider_lab("environment", None):
            raise RuntimeError("test failed")
    assert events == ["remote cleanup", "lab removed"]
