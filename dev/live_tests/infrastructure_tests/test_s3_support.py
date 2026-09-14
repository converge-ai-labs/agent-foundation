"""Private S3 credential isolation and opt-in validation without network calls."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ..performance.s3_config import S3ConnectionConfig, load_s3_config, open_s3_client
from ..providers import provider_config
from ..providers.provider_config import load_provider_settings


def config_values():
    return {
        "dedicated_test_bucket": True,
        "endpoint_url": "http://127.0.0.1:9000",
        "region": "us-east-1",
        "bucket": "fixture-test",
        "access_key": "fixture-access",
        "secret_key": "fixture-secret",
    }


def write_config(path, values):
    path.write_text("[s3]\n" + "\n".join(f"{key} = {json.dumps(value)}" for key, value in values.items()) + "\n")
    path.chmod(0o600)


def test_config_gate_does_not_even_inspect_path_or_ambient_config(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Disabled benchmark inspected configuration")

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient-production")
    monkeypatch.setenv("A13N_SERVICE_OBJECT_BACKEND", "s3")
    monkeypatch.setattr(Path, "exists", forbidden)
    assert load_s3_config(enabled=False) is None


def test_absent_dedicated_config_skips_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient-production")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-production")
    monkeypatch.delenv("LIVE_TEST_PROVIDERS_CONFIG", raising=False)
    monkeypatch.setattr(provider_config, "DEFAULT_PATH", tmp_path / "missing.toml")
    assert load_s3_config(enabled=True) is None


def test_private_config_and_example(tmp_path):
    values = config_values()
    path = tmp_path / "providers.local.toml"
    write_config(path, values)
    config = load_s3_config(enabled=True, path=path)
    assert config.bucket == values["bucket"]
    assert "fixture-secret" not in repr(config)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private regular file"):
        load_s3_config(enabled=True, path=path)
    example = Path(__file__).resolve().parents[1] / "providers.example.toml"
    private_example = tmp_path / "example.toml"
    private_example.write_bytes(example.read_bytes())
    private_example.chmod(0o600)
    assert load_s3_config(enabled=True, path=private_example) is None


def test_shared_config_override_and_sections_are_independent(tmp_path, monkeypatch):
    path = tmp_path / "providers.local.toml"
    write_config(path, config_values())
    with path.open("a") as output:
        output.write('[search]\nprovider="exa"\napi_key="search-secret"\n')
    monkeypatch.setenv("LIVE_TEST_PROVIDERS_CONFIG", str(path))
    assert load_s3_config(enabled=True).bucket == "fixture-test"
    assert load_provider_settings().search.api_key.get_secret_value() == "search-secret"
    # Each opt-in validates its own settings, without enabling another integration.
    path.write_text('[search]\nprovider="exa"\napi_key="search-secret"\n[s3]\nsecret_key="partial-secret"\n')
    assert load_provider_settings().search.provider == "exa"
    with pytest.raises(ValueError, match="Invalid S3 benchmark") as caught:
        load_s3_config(enabled=True)
    assert "partial-secret" not in str(caught.value)


@pytest.mark.parametrize("section", ["", "[s3]\n", '[s3]\nendpoint_url=""\nsecret_key="  "\n'])
def test_absent_or_blank_s3_ignores_other_provider_sections(tmp_path, section):
    path = tmp_path / "providers.local.toml"
    path.write_text('[model]\nprovider="partial-model"\n' + section)
    path.chmod(0o600)
    assert load_s3_config(enabled=True, path=path) is None


@pytest.mark.parametrize("via_override", [False, True])
def test_explicit_missing_shared_config_is_an_error(tmp_path, monkeypatch, via_override):
    path = tmp_path / "missing.toml"
    monkeypatch.setenv("LIVE_TEST_PROVIDERS_CONFIG", str(path))
    with pytest.raises(ValueError, match="private regular file"):
        load_s3_config(enabled=True, path=None if via_override else path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("access_key", ""),
        ("secret_key", "  "),
        ("dedicated_test_bucket", False),
        ("endpoint_url", "http://remote.example"),
        ("endpoint_url", "https://secret:password@remote.example"),
        ("endpoint_url", "https://remote.example?secret=value"),
        ("concurrency", [0]),
        ("concurrency", [65]),
        ("concurrency", [1, 1]),
        ("concurrency", [True]),
        ("rounds", 0),
        ("p95_write_slo_ms", -1),
        ("p99_write_slo_ms", float("inf")),
        ("profile", "production"),
    ],
)
def test_invalid_configuration_is_rejected(field, value):
    with pytest.raises(ValidationError):
        S3ConnectionConfig.model_validate({**config_values(), field: value})


def test_invalid_config_never_quotes_secrets(tmp_path):
    path = tmp_path / "providers.local.toml"
    path.write_text('secret_key = "secret-never-log\n')
    path.chmod(0o600)
    with pytest.raises(ValueError, match="Invalid S3 benchmark") as caught:
        load_s3_config(enabled=True, path=path)
    assert "secret-never-log" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_config_rejects_symlink(tmp_path):
    target = tmp_path / "credentials.toml"
    write_config(target, config_values())
    link = tmp_path / "providers.local.toml"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="private regular file"):
        load_s3_config(enabled=True, path=link)


@pytest.mark.anyio
async def test_client_construction_ignores_ambient_aws_and_service_config(monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "ambient-profile-must-not-exist")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/ambient-file-must-not-be-read")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/ambient-creds-must-not-be-read")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://must-not-be-used.invalid")
    monkeypatch.setenv("AWS_REGION", "invalid-region")
    monkeypatch.setenv("A13N_SERVICE_OBJECT_BUCKET", "production")
    config = S3ConnectionConfig.model_validate(config_values())
    # Construction only: this test makes no HTTP request.
    async with open_s3_client(config) as client:
        assert client.meta.endpoint_url == config.endpoint_url
        assert client.meta.region_name == config.region
        assert client.meta.config.proxies == {}
