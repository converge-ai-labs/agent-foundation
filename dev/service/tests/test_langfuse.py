"""Local trace wiring has one TOML source and cannot target ambient collectors."""

import base64
import dataclasses
import os
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from a13n_service.configuration.sources import load_settings

from dev.service import langfuse as langfuse_module
from dev.service.environment import LOCAL_CONFIG
from dev.service.langfuse import SHARED_PROJECT, Langfuse, local_traces, trace_environment
from dev.service.tests.support import environment_for


def local_langfuse(tmp_path, **query):
    settings = load_settings(
        LOCAL_CONFIG,
        environ={},
        overrides={
            "objects": {"local_root": tmp_path / "var/service/objects"},
            "filesystem": {"root": tmp_path / "var/service/files"},
            "observability": {"query": query},
        },
    )
    return Langfuse(environment_for(settings, tmp_path))


@pytest.mark.parametrize(
    "query",
    [
        {"provider": "remote"},
        {"langfuse_base_url": "https://cloud.langfuse.com"},
        {"langfuse_base_url": "http://localhost:3000"},
        {"langfuse_base_url": "http://127.0.0.1:3000/api"},
        {"langfuse_base_url": "http://user:secret@127.0.0.1:3000"},
        {"langfuse_base_url": "http://127.0.0.1:3000?remote=true"},
        {"langfuse_base_url": "http://127.0.0.1:65535"},
        {"langfuse_base_url": "http://127.0.0.1:7999"},
        {"langfuse_public_key": "incompatible"},
        {"langfuse_secret_key": "incompatible"},
        {"langfuse_base_url": "http://127.0.0.1:15432"},
        {"langfuse_public_key": None},
        {"langfuse_secret_key": None},
    ],
)
def test_reject_nonlocal_or_conflicting_configuration_before_compose(tmp_path, query, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError):
        local_langfuse(tmp_path, **query)._compose(tmp_path / "shared.yaml", "up")
    assert calls == []


def test_compose_uses_selected_toml_and_checkout_not_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LANGFUSE_LOCAL_PORT=9999\n")
    monkeypatch.setenv("LANGFUSE_LOCAL_PORT", "8888")
    monkeypatch.setenv("LANGFUSE_LOCAL_MINIO_PORT", "8889")
    monkeypatch.setenv("LANGFUSE_LOCAL_PUBLIC_KEY", "ambient-key")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="ready")

    monkeypatch.setattr(subprocess, "run", run)
    langfuse = local_langfuse(tmp_path)
    shared = tmp_path / "shared.yaml"
    shared.write_text("services: {}\n")
    assert langfuse._compose(shared, "ps", capture=True) == "ready"
    command, options = calls[0]
    assert command[command.index("--env-file") + 1] == os.devnull
    assert command[command.index("--project-name") + 1] == SHARED_PROJECT
    assert command[command.index("--file") + 1] == str(shared)
    assert options["env"]["LANGFUSE_LOCAL_PORT"] == "3000"
    assert options["env"]["LANGFUSE_LOCAL_MINIO_PORT"] == "3001"
    assert options["env"]["LANGFUSE_LOCAL_PUBLIC_KEY"] == "lf_pk_agent_foundation_local"
    assert options["env"]["LANGFUSE_LOCAL_SECRET_KEY"] == "lf_sk_agent_foundation_local"
    assert (tmp_path / ".env").read_text() == "LANGFUSE_LOCAL_PORT=9999\n"


def test_standard_export_profile_replaces_all_ambient_otel_settings(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "https://remote.invalid/v1/traces")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "Authorization=ambient-secret")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_off")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "private=ambient-value")
    monkeypatch.setenv("UNRELATED_VARIABLE", "preserve")
    langfuse = local_langfuse(tmp_path)
    env = trace_environment(langfuse)
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:3000/api/public/otel"
    assert env["OTEL_TRACES_EXPORTER"] == "otlp"
    assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert env["OTEL_TRACES_SAMPLER"] == "always_on"
    assert env["OTEL_BSP_SCHEDULE_DELAY"] == "500"
    assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" not in env
    assert "OTEL_EXPORTER_OTLP_TRACES_HEADERS" not in env
    assert env["OTEL_RESOURCE_ATTRIBUTES"] == (
        "deployment.environment.name=" + langfuse.environment.settings.service.deployment_environment_name
    )
    expected = base64.b64encode(b"lf_pk_agent_foundation_local:lf_sk_agent_foundation_local").decode()
    assert env["OTEL_EXPORTER_OTLP_HEADERS"] == f"Authorization=Basic%20{expected},x-langfuse-ingestion-version=4"
    before = dict(os.environ)
    with pytest.raises(RuntimeError, match="intentional"):
        with local_traces(langfuse):
            assert os.environ["OTEL_TRACES_EXPORTER"] == "otlp"
            assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" not in os.environ
            assert os.environ["UNRELATED_VARIABLE"] == "preserve"
            raise RuntimeError("intentional")
    assert dict(os.environ) == before
    message = capsys.readouterr().out
    assert "replaces inherited OTEL_*" in message
    assert "ambient-secret" not in message and "remote.invalid" not in message


@pytest.mark.parametrize("disabled", ["tracing", "query"])
def test_disabled_traces_cannot_fall_back_to_ambient_exporter(tmp_path, monkeypatch, disabled):
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://remote.invalid")
    langfuse = local_langfuse(tmp_path, **({"provider": "none"} if disabled == "query" else {}))
    if disabled == "tracing":
        settings = langfuse.environment.settings
        changed = settings.model_copy(
            update={"observability": settings.observability.model_copy(update={"tracing": False})}
        )
        langfuse = Langfuse(dataclasses.replace(langfuse.environment, settings=changed))
    env = trace_environment(langfuse)
    assert env["OTEL_TRACES_EXPORTER"] == "none"
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in env


def test_start_checks_readiness_then_project_authentication(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("dev.service.docker.ensure_docker", lambda: calls.append("docker"))
    monkeypatch.setattr(Langfuse, "_shared_compose", lambda self, **kwargs: tmp_path / "shared.yaml")
    monkeypatch.setattr(Langfuse, "_compose", lambda self, path, *args: calls.append(args))
    monkeypatch.setattr(Langfuse, "check_credentials", lambda self: calls.append("authenticate"))
    local_langfuse(tmp_path).start()
    assert calls == ["docker", ("up", "-d", "--wait", "--wait-timeout", "180"), "authenticate"]
    calls.clear()
    local_langfuse(tmp_path, provider="none").start()
    assert calls == []


def test_incompatible_second_source_cannot_mutate_shared_stack(tmp_path, monkeypatch):
    machine = tmp_path / "machine"
    source_root = tmp_path / "source"
    source = source_root / "dev/observability/langfuse.compose.yaml"
    source.parent.mkdir(parents=True)
    source.write_text("services: {web: {image: first}}\n")
    monkeypatch.setattr(langfuse_module, "ROOT", source_root)
    monkeypatch.setattr(langfuse_module, "_machine_directory", lambda: machine)
    monkeypatch.setattr("dev.service.docker.ensure_docker", lambda: None)
    monkeypatch.setattr(Langfuse, "_project_resources", lambda self: ())
    mutations = []
    monkeypatch.setattr(Langfuse, "_compose", lambda self, path, *args: mutations.append((path, args)))
    monkeypatch.setattr(Langfuse, "check_credentials", lambda self: None)
    langfuse = local_langfuse(tmp_path)
    langfuse.start()
    assert len(mutations) == 1
    source.write_text("services: {web: {image: incompatible}}\n")
    with pytest.raises(ValueError, match=r"incompatible.*no resources were changed"):
        langfuse.start()
    assert len(mutations) == 1
    langfuse.stop()
    with pytest.raises(ValueError, match="incompatible"):
        langfuse.start()
    langfuse.stop(reset=True)
    langfuse.start()
    assert (machine / "langfuse-v2.compose.yaml").read_bytes() == source.read_bytes()


def test_partial_shared_config_retries_before_first_mutation(tmp_path, monkeypatch):
    machine = tmp_path / "machine"
    source_root = tmp_path / "source"
    source = source_root / "dev/observability/langfuse.compose.yaml"
    source.parent.mkdir(parents=True)
    source.write_text("services: {}\n")
    machine.mkdir()
    (machine / "langfuse-v2.compose.yaml").write_bytes(source.read_bytes())
    monkeypatch.setattr(langfuse_module, "ROOT", source_root)
    monkeypatch.setattr(langfuse_module, "_machine_directory", lambda: machine)
    monkeypatch.setattr("dev.service.docker.ensure_docker", lambda: None)
    monkeypatch.setattr(Langfuse, "_project_resources", lambda self: ())
    calls = []
    monkeypatch.setattr(Langfuse, "_compose", lambda self, path, *args: calls.append((path, args)))
    monkeypatch.setattr(Langfuse, "check_credentials", lambda self: None)
    local_langfuse(tmp_path).start()
    assert (machine / "langfuse-v2.json").is_file()
    assert calls[0][0] == machine / "langfuse-v2.compose.yaml"


@pytest.mark.parametrize("status", [200, 401, 503])
def test_authentication_uses_local_credentials_without_proxy_or_secret_errors(tmp_path, monkeypatch, status):
    original_client = httpx2.Client
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(status, json={"data": []})

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original_client(transport=httpx2.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx2, "Client", client)
    langfuse = local_langfuse(tmp_path, langfuse_secret_key="private-test-secret")
    if status == 200:
        langfuse.check_credentials()
    else:
        with pytest.raises(RuntimeError, match="authentication failed") as error:
            langfuse.check_credentials()
        assert "private-test-secret" not in str(error.value)
    assert str(requests[0].url) == "http://127.0.0.1:3000/api/public/projects"
    assert requests[0].headers["Authorization"].startswith("Basic ")


def test_smoke_test_uses_selected_project_environment(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(Langfuse, "start", lambda self: calls.append("start"))
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    langfuse = local_langfuse(tmp_path)
    langfuse.test()
    assert calls[0] == "start"
    command, options = calls[1]
    assert command[-1] == "packages/a13n-service/tests/trace_query/test_langfuse_integration.py"
    assert options["env"]["A13N_TEST_LANGFUSE_BASE_URL"] == "http://127.0.0.1:3000"
    assert options["env"]["A13N_TEST_LANGFUSE_PUBLIC_KEY"] == "lf_pk_agent_foundation_local"


def test_make_uses_explicit_configuration_without_service_dotenv():
    root = Path(__file__).resolve().parents[3]
    makefile = (root / "Makefile").read_text()
    assert "LANGFUSE_COMPOSE" not in makefile
    for action in ("up", "down", "reset", "test"):
        assert f"$(SERVICE_DEV) langfuse {action}" in makefile
    assert "SERVICE_CONFIG ?= dev/service/local.toml" in makefile
    assert not (root / ".env.example").exists()
    assert (root / ".env.harness.example").exists()


def test_local_export_and_query_bypass_ambient_proxies_and_restore_them(tmp_path, monkeypatch):
    from requests import Session

    monkeypatch.setenv("HTTP_PROXY", "http://upper-proxy.invalid:8080")
    monkeypatch.setenv("http_proxy", "http://lower-proxy.invalid:8080")
    monkeypatch.setenv("ALL_PROXY", "http://all-proxy.invalid:8080")
    monkeypatch.setenv("NO_PROXY", "upper.internal")
    monkeypatch.setenv("no_proxy", "lower.internal")
    before = dict(os.environ)
    langfuse = local_langfuse(tmp_path)
    with local_traces(langfuse), Session() as client:
        assert os.environ["NO_PROXY"] == os.environ["no_proxy"]
        assert "upper.internal" in os.environ["no_proxy"]
        assert "lower.internal" in os.environ["no_proxy"]
        options = client.merge_environment_settings(
            langfuse.base_url + "/api/public/otel/v1/traces", {}, False, None, None
        )
        assert options["proxies"] == {}
        remote = client.merge_environment_settings("http://remote.invalid", {}, False, None, None)
        assert remote["proxies"]["http"] == "http://lower-proxy.invalid:8080"
    assert dict(os.environ) == before


@pytest.mark.parametrize("provider", ["none", "logfire"])
def test_disabled_stack_can_be_stopped_without_ambient_compose_settings(tmp_path, monkeypatch, provider):
    monkeypatch.setenv("LANGFUSE_LOCAL_PORT", "9999")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(subprocess, "run", run)
    langfuse = local_langfuse(
        tmp_path,
        provider=provider,
        logfire_base_url="https://logfire-us.pydantic.dev",
        logfire_read_token="test-read-token",
        logfire_history_from="2026-09-14T00:00:00Z",
    )
    shared = tmp_path / "shared.yaml"
    shared.write_text("services: {}\n")
    langfuse._compose(shared, "stop")
    command, options = calls[0]
    assert command[-1] == "stop"
    assert command[command.index("--project-name") + 1] == SHARED_PROJECT
    assert "LANGFUSE_LOCAL_PORT" not in options["env"]


@pytest.mark.parametrize("region", ["us", "eu"])
def test_logfire_query_selects_matching_export_without_starting_langfuse(tmp_path, monkeypatch, region):
    monkeypatch.delenv("A13N_DEV_TRACE_BACKEND", raising=False)
    monkeypatch.delenv("LOGFIRE_BASE_URL", raising=False)
    monkeypatch.setenv("LOGFIRE_TOKEN", "test-write-token")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "https://ambient.invalid/v1/traces")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "Authorization=ambient-secret")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("must not operate Langfuse"))
    base_url = f"https://logfire-{region}.pydantic.dev"
    langfuse = local_langfuse(
        tmp_path,
        provider="logfire",
        logfire_base_url=base_url + "/",
        logfire_read_token="test-read-token",
        logfire_history_from="2026-09-14T00:00:00Z",
    )
    langfuse.validate()
    langfuse.start()
    assert not langfuse.enabled
    env = trace_environment(langfuse)
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == base_url
    assert env["OTEL_EXPORTER_OTLP_HEADERS"] == "Authorization=test-write-token"
    assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert env["OTEL_TRACES_EXPORTER"] == "otlp"
    assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" not in env
    assert "OTEL_EXPORTER_OTLP_TRACES_HEADERS" not in env
    assert "test-read-token" not in env["OTEL_EXPORTER_OTLP_HEADERS"]
    monkeypatch.setenv("A13N_DEV_TRACE_BACKEND", "none")
    assert trace_environment(langfuse)["OTEL_TRACES_EXPORTER"] == "none"


@pytest.mark.parametrize("field", ["logfire_base_url", "logfire_read_token", "logfire_history_from"])
def test_logfire_query_requires_complete_configuration_before_infrastructure(tmp_path, monkeypatch, field):
    query = {
        "provider": "logfire",
        "logfire_base_url": "https://logfire-us.pydantic.dev",
        "logfire_read_token": "test-read-token",
        "logfire_history_from": "2026-09-14T00:00:00Z",
    }
    query[field] = None
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("must not operate infrastructure"))
    with pytest.raises(ValueError, match=field.upper()):
        local_langfuse(tmp_path, **query).validate()


def test_commented_logfire_recipe_is_valid_without_changing_public_default():
    text = LOCAL_CONFIG.read_text()
    assert tomllib.loads(text)["observability"]["query"]["provider"] == "langfuse"
    recipe = tomllib.loads(
        "\n".join(
            line.removeprefix("# ")
            for line in text.splitlines()
            if line.startswith(('# provider = "logfire"', "# logfire_"))
        )
    )
    settings = load_settings(LOCAL_CONFIG, environ={}, overrides={"observability": {"query": recipe}})
    settings.validate_trace_query_configuration()
    assert settings.observability.query.provider == "logfire"
    assert settings.observability.query.logfire_base_url == "https://logfire-us.pydantic.dev"


def test_logfire_export_only_retains_explicit_profile_and_requires_write_token(tmp_path, monkeypatch):
    monkeypatch.setenv("A13N_DEV_TRACE_BACKEND", "logfire")
    monkeypatch.setenv("LOGFIRE_BASE_URL", "https://logfire-eu.pydantic.dev/")
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    langfuse = local_langfuse(tmp_path, provider="none")
    with pytest.raises(ValueError, match="LOGFIRE_TOKEN"):
        trace_environment(langfuse)
    monkeypatch.setenv("LOGFIRE_TOKEN", "test-write-token")
    env = trace_environment(langfuse)
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "https://logfire-eu.pydantic.dev"
    assert env["OTEL_EXPORTER_OTLP_HEADERS"] == "Authorization=test-write-token"
    before = dict(os.environ)
    with local_traces(langfuse):
        assert os.environ["OTEL_EXPORTER_OTLP_HEADERS"] == "Authorization=test-write-token"
    assert dict(os.environ) == before
