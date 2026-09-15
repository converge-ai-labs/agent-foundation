"""The executable's explicit sources, precedence and safe diagnostics."""

import base64

import pytest
from a13n_service.cli import main
from a13n_service.configuration.sources import ConfigurationError, configuration_fields, load_settings
from a13n_service.settings import Settings
from click.testing import CliRunner
from pydantic import ValidationError


def test_nested_file_environment_and_cli_precedence(tmp_path):
    path = tmp_path / "service.toml"
    path.write_text(
        '[service]\nport=9000\nrole="worker"\n[observability.query]\nprovider="none"\n[objects]\nlocal_root="objects"\n'
    )
    value = load_settings(
        path,
        environ={"A13N_SERVICE_PORT": "9001", "A13N_SERVICE_ROLE": "control"},
        overrides={"service": {"role": "all"}},
    )
    assert value.service.port == 9001
    assert value.service.role == "all"
    assert value.objects.local_root == tmp_path / "objects"
    assert value.observability.query.provider == "none"
    with pytest.raises(ValidationError):
        value.service.port = 9999


def test_no_implicit_files_and_no_environment_in_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name, text in ((".env", "A13N_SERVICE_PORT=9010"), ("a13n-service.toml", "[service]\nport=9011")):
        (tmp_path / name).write_text(text)
    monkeypatch.setenv("A13N_SERVICE_PORT", "9012")
    assert Settings().service.port == 8000
    assert load_settings().service.port == 9012
    assert load_settings(environ={}).service.port == 8000


def test_relative_paths_share_file_base_even_for_environment_overrides(tmp_path, monkeypatch):
    path = tmp_path / "service.toml"
    path.write_text('[database]\nurl="postgresql://db.example/service"\n')
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    settings = load_settings(path, environ={"A13N_SERVICE_OBJECT_LOCAL_ROOT": "content"})
    assert settings.database.url is not None
    assert settings.database.url.get_secret_value() == "postgresql://db.example/service"
    assert settings.objects.local_root == tmp_path / "content"
    assert settings.filesystem.root == tmp_path / "var/files"


@pytest.mark.parametrize(
    "document",
    [
        '[databse]\nurl="postgresql://db.example/service"',
        '[database]\nurls="postgresql://db.example/service"',
        '[observability.query]\nprovidre="none"',
    ],
)
def test_unknown_toml_fields_are_not_hidden_by_overrides(tmp_path, document):
    path = tmp_path / "service.toml"
    path.write_text(document)
    with pytest.raises(ConfigurationError, match="Unknown configuration field"):
        load_settings(path, environ={"A13N_SERVICE_DATABASE_URL": "postgresql://db.example/override"})


def test_explicit_missing_or_invalid_toml_fails(tmp_path):
    path = tmp_path / "service.toml"
    with pytest.raises(ConfigurationError):
        load_settings(path, environ={})
    path.write_text('password = "private-value\n')
    with pytest.raises(ConfigurationError) as error:
        load_settings(path, environ={})
    assert "private-value" not in str(error.value)


def test_sensitive_inputs_are_redacted_from_errors_and_representations(tmp_path):
    path = tmp_path / "service.toml"
    path.write_text(
        '[secrets]\nmaster_key_base64="' + base64.b64encode(b"x" * 32).decode() + '"\nencryption_key_id="local"\n'
    )
    settings = load_settings(
        path, environ={"A13N_SERVICE_DATABASE_URL": "postgresql://admin:private-pass@localhost/db"}
    )
    assert "private-pass" not in repr(settings)
    path.write_text('[service]\nport="private-invalid-port"\n')
    result = CliRunner().invoke(main, ["--config", str(path), "config", "check"])
    assert result.exit_code == 1
    assert "service.port" in result.output
    assert "private-invalid-port" not in result.output


def test_environment_catalog_is_unique_and_preserves_known_names():
    fields = {name: path for name, path, _ in configuration_fields(Settings)}
    assert len(fields) == len(list(configuration_fields(Settings)))
    assert fields["A13N_SERVICE_OBJECT_LOCAL_ROOT"] == ("objects", "local_root")
    assert fields["A13N_SERVICE_AUTO_MIGRATE"] == ("migration", "auto_migrate")
    assert fields["A13N_SERVICE_A2A_ENABLED"] == ("gateway", "a2a_enabled")
    assert fields["A13N_SERVICE_CONNECTIVITY_AUTHORIZATION_CALLBACK_URLS"] == (
        "connectivity",
        "authorization_callback_urls",
    )
    assert fields["A13N_SERVICE_CONNECTIVITY_MCP_SERVERS"] == ("connectivity", "mcp_servers")
    assert fields["A13N_SERVICE_OBSERVABILITY_QUERY_PROVIDER"] == ("observability", "query", "provider")
    assert fields["A13N_SERVICE_PROVIDER_PLUGIN_ENABLED"] == ("provider_plugins", "enabled")


def test_arrays_use_native_toml_and_json_environment(tmp_path):
    path = tmp_path / "service.toml"
    path.write_text('[models]\nprivate_endpoint_cidrs=["127.0.0.1/32"]\n')
    assert load_settings(path, environ={}).models.private_endpoint_cidrs == ("127.0.0.1/32",)
    assert load_settings(
        path, environ={"A13N_SERVICE_MODEL_PRIVATE_ENDPOINT_CIDRS": '["10.0.0.0/8"]'}
    ).models.private_endpoint_cidrs == ("10.0.0.0/8",)
    assert load_settings(
        path, environ={"A13N_SERVICE_PROVIDER_PLUGIN_ENABLED": '["acme"]'}
    ).provider_plugins.enabled == ("acme",)
    with pytest.raises(ConfigurationError, match="Invalid JSON array"):
        load_settings(path, environ={"A13N_SERVICE_MODEL_PRIVATE_ENDPOINT_CIDRS": "hidden-invalid-value"})


def test_cli_override_applies_before_validation(tmp_path, monkeypatch):
    path = tmp_path / "service.toml"
    path.write_text('[service]\nrole="invalid"\n')
    captured = []
    monkeypatch.setattr("a13n_service.cli._prepare_database", lambda settings: None)
    monkeypatch.setattr("a13n_service.cli.serve_app", lambda app, **kwargs: captured.append(kwargs))
    result = CliRunner().invoke(main, ["--config", str(path), "serve", "--role", "worker", "--host", "127.0.0.2"])
    assert result.exit_code == 0, result.output
    assert captured[0]["host"] == "127.0.0.2"
