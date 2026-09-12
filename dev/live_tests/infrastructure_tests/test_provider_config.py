"""Offline configuration partitions and cleanup after partial/failed integration."""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ..providers import provider_config
from ..providers.provider_config import ProviderSettings, load_provider_settings


@pytest.mark.parametrize("upstream_model", [None, "openai/gpt-4.1-nano"])
def test_missing_default_and_blank_example_preserve_defaults(tmp_path, monkeypatch, upstream_model):
    monkeypatch.delenv("LIVE_TEST_PROVIDERS_CONFIG", raising=False)
    monkeypatch.setattr(provider_config, "DEFAULT_PATH", tmp_path / "missing.toml")
    assert load_provider_settings(upstream_model=upstream_model) == ProviderSettings()
    assert (
        load_provider_settings(Path(__file__).parents[1] / "providers.example.toml", upstream_model=upstream_model)
        == ProviderSettings()
    )


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
        '[connector]\nprovider="composio"\napi_key="key"\nproject_api_key="sample-secret"',
        '[connector]\nprovider="unknown"\napi_key="sample-secret"',
        '[model]\nprovider="openrouter"\napi_key="sample-secret"',
        '[model]\nprovider="openrouter"\napi_key="sample-secret"\nmodel=""',
        '[search]\nprovider="exa"',
        '[search]\napi_key="sample-secret"',
        '[search]\nprovider="unknown"\napi_key="sample-secret"',
        '[search]\nprovider="exa"\napi_key="sample-secret"\nbase_url="https://example.com"',
        '[brave_search]\nprovider="brave"',
        '[brave_search]\napi_key="sample-secret"',
        '[brave_search]\nprovider="exa"\napi_key="sample-secret"',
        '[brave_search]\nprovider="brave"\napi_key="sample-secret"\nbase_url="https://example.com"',
        '[model]\nprovider="openai"\napi_key="sample-secret"\nmodel="model"\nbase_url="https://sample-secret@example.com"',
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


@pytest.mark.parametrize("provider", ["openrouter", "openai"])
def test_enabled_model_and_connector_sections(tmp_path, provider):
    path = tmp_path / "settings.toml"
    endpoint = 'base_url="https://models.example/v1"' if provider == "openai" else ""
    path.write_text(
        '[connector]\nprovider="composio"\napi_key="connector-key"\n'
        f'[model]\nprovider="{provider}"\napi_key="model-key"\nmodel="upstream/model"\n{endpoint}\n'
    )
    config = load_provider_settings(path)
    assert config.connector.provider == "composio"
    assert config.model.model == "upstream/model" and config.model.provider == provider
    assert config.environment is None


@pytest.mark.parametrize("section,provider", [("search", "exa"), ("brave_search", "brave")])
def test_search_configuration_is_independent_and_redacted(tmp_path, section, provider):
    path = tmp_path / "settings.toml"
    path.write_text(f'[{section}]\nprovider="{provider}"\napi_key="search-secret"\n')
    config = load_provider_settings(path)
    selected = getattr(config, section)
    assert selected.provider == provider
    assert selected.api_key.get_secret_value() == "search-secret"
    assert getattr(config, "brave_search" if section == "search" else "search") is None
    assert config.environment is None and config.connector is None and config.model is None
    assert "search-secret" not in repr(config) + config.model_dump_json()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "section,flag",
    [
        ("model", ""),
        ("search", ""),
        ("brave_search", ""),
        (("model", "openai/gpt-4.1-nano"), ""),
    ],
)
async def test_offline_fixture_does_not_read_private_configuration(monkeypatch, section, flag):
    from ..conftest import configured_provider

    def unexpected_read():
        raise AssertionError("Offline collection read private configuration")

    monkeypatch.setattr(provider_config, "load_provider_settings", unexpected_read)
    request = SimpleNamespace(config=SimpleNamespace(getoption=lambda option: option == flag), param=section)
    fixture = configured_provider.__wrapped__(request)
    with pytest.raises(pytest.skip.Exception):
        await anext(fixture)


@pytest.mark.anyio
@pytest.mark.parametrize("section", ["search", "brave_search"])
async def test_unconfigured_search_skips_before_starting_lab(monkeypatch, section):
    from ..conftest import configured_provider
    from ..providers import real_providers

    monkeypatch.setattr(provider_config, "load_provider_settings", ProviderSettings)

    def unexpected_lab(*args):
        raise AssertionError("Unconfigured search started live infrastructure")

    monkeypatch.setattr(real_providers, "configured_provider_lab", unexpected_lab)
    request = SimpleNamespace(
        config=SimpleNamespace(getoption=lambda option: option == "--live-providers"), param=section
    )
    with pytest.raises(pytest.skip.Exception, match=f"Optional {section} Provider is not configured"):
        await anext(configured_provider.__wrapped__(request))


@pytest.mark.anyio
@pytest.mark.parametrize("section,provider", [("search", "exa"), ("brave_search", "brave")])
async def test_search_fixture_routes_each_configured_account_to_its_own_provider(
    tmp_path, monkeypatch, section, provider
):
    from ..conftest import configured_provider
    from ..providers import real_providers

    path = tmp_path / "settings.toml"
    path.write_text(
        '[search]\nprovider="exa"\napi_key="exa-secret"\n[brave_search]\nprovider="brave"\napi_key="brave-secret"\n'
    )
    settings = load_provider_settings(path)
    assert "exa-secret" not in settings.model_dump_json() and "brave-secret" not in repr(settings)
    monkeypatch.setattr(provider_config, "load_provider_settings", lambda: settings)

    @asynccontextmanager
    async def lab(kind, selected):
        assert kind == "search"
        assert selected is getattr(settings, section)
        assert selected.provider == provider
        assert selected.api_key.get_secret_value() == provider + "-secret"
        yield "search journey"

    monkeypatch.setattr(real_providers, "configured_provider_lab", lab)
    request = SimpleNamespace(
        config=SimpleNamespace(getoption=lambda option: option == "--live-providers"), param=section
    )
    fixture = configured_provider.__wrapped__(request)
    try:
        assert await anext(fixture) == "search journey"
    finally:
        await fixture.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider,model_setting",
    [
        ("openrouter", ""),
        ("openrouter", 'model=""'),
        ("openrouter", 'model="configured/model"'),
        ("openrouter", "model=123"),
        ("openai", 'model="configured/model"'),
    ],
)
async def test_model_matrix_uses_openrouter_credentials_without_changing_config(
    tmp_path, monkeypatch, provider, model_setting
):
    from ..conftest import configured_provider
    from ..providers import real_providers

    path = tmp_path / "settings.toml"
    endpoint = 'base_url="https://models.example/v1"' if provider == "openai" else ""
    source = f'[model]\nprovider="{provider}"\napi_key="model-secret"\n{model_setting}\n{endpoint}\n'
    path.write_text(source)
    monkeypatch.setenv("LIVE_TEST_PROVIDERS_CONFIG", str(path))
    provisioned = []

    @asynccontextmanager
    async def lab(section, selected):
        provisioned.append((section, selected))
        yield "configured"

    monkeypatch.setattr(real_providers, "configured_provider_lab", lab)
    request = SimpleNamespace(
        config=SimpleNamespace(getoption=lambda option: option == "--live-providers"),
        param=("model", "google/gemini-2.5-flash-lite"),
    )
    fixture = configured_provider.__wrapped__(request)
    try:
        if provider == "openai":
            with pytest.raises(pytest.skip.Exception, match=r"requires model\.provider=openrouter"):
                await anext(fixture)
            assert provisioned == []
        else:
            assert await anext(fixture) == "configured"
            section, selected = provisioned[0]
            assert section == "model" and selected.model == "google/gemini-2.5-flash-lite"
            assert selected.api_key.get_secret_value() == "model-secret"
            assert "model-secret" not in repr(selected)
        assert path.read_text() == source
    finally:
        await fixture.aclose()


@pytest.mark.parametrize(
    "settings",
    [
        'provider="openrouter"',
        'provider="openrouter"\napi_key=""',
        'provider="openrouter"\napi_key="sample-secret"\nbase_url="https://sample-secret@models.example/v1"',
        'provider="unknown"\napi_key="sample-secret"',
    ],
)
def test_fixed_model_still_validates_provider_credentials_and_endpoint(tmp_path, settings):
    path = tmp_path / "settings.toml"
    path.write_text("[model]\n" + settings)
    with pytest.raises(ValueError, match="Provider configuration") as caught:
        load_provider_settings(path, upstream_model="openai/gpt-4.1-nano")
    assert "sample-secret" not in str(caught.value)


@pytest.mark.anyio
async def test_cleanup_attempts_every_owned_environment_even_after_run_failure():
    from ..providers.real_providers import cleanup_provider_lab

    live = SimpleNamespace(
        cleanup=AsyncMock(side_effect=RuntimeError("Run failure")),
        collection=AsyncMock(
            return_value=[
                {"id": "env_first", "status": "running", "provider_id": "ep_remote"},
                {"id": "env_local", "status": "running", "provider_id": "ep_local"},
                {"id": "env_second", "status": "unprepared", "provider_id": "ep_remote"},
            ]
        ),
        request=AsyncMock(side_effect=[{"type": "a13n.e2b"}, {"type": "a13n.direct-local"}, {"type": "a13n.e2b"}]),
    )
    journey = SimpleNamespace(
        live=live,
        base="/api/v1/workspaces/ws_owned",
        environment_command=AsyncMock(side_effect=[RuntimeError("first failed"), {"status": "deleted"}]),
    )
    with pytest.raises(AssertionError, match="env_first"):
        await cleanup_provider_lab(journey)
    assert journey.environment_command.await_count == 2, "Direct Local must not receive unsupported delete commands"
    assert journey.environment_command.await_args_list[1].args == ("env_second", "delete")
    live.collection.assert_awaited_once_with(journey.base + "/environments")


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["provision", "test"])
@pytest.mark.parametrize("section", ["environment", "connector"])
async def test_provider_cleanup_precedes_lab_teardown_after_failure(monkeypatch, failure, section):
    from ..providers import real_providers

    events = []

    @asynccontextmanager
    async def lab(**kwargs):
        # Configured Connector accounts must never be routed to the scripted peer.
        assert kwargs["local_connectors"] == (section != "connector")
        try:
            yield SimpleNamespace(client=SimpleNamespace(config={"workspace_id": "ws_owned"}, http=SimpleNamespace()))
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
        async with real_providers.configured_provider_lab(section, None):
            raise RuntimeError("test failed")
    assert events == ["remote cleanup", "lab removed"]
