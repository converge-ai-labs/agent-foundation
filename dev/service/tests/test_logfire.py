"""The opt-in Logfire dev export profile must replace, not inherit, collector settings."""

import dataclasses

import pytest

from dev.service.langfuse import Langfuse, trace_environment
from dev.service.tests.test_langfuse import local_langfuse


def test_explicit_logfire_profile_works_without_langfuse_query(tmp_path, monkeypatch):
    monkeypatch.setenv("A13N_DEV_TRACE_BACKEND", "logfire")
    monkeypatch.setenv("LOGFIRE_TOKEN", "fictional-write-token")
    monkeypatch.setenv("LOGFIRE_BASE_URL", "https://logfire-eu.pydantic.dev")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "https://unselected.invalid/v1/traces")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "Authorization=unselected")
    env = trace_environment(local_langfuse(tmp_path, provider="none"))
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "https://logfire-eu.pydantic.dev"
    assert env["OTEL_EXPORTER_OTLP_HEADERS"] == "Authorization=fictional-write-token"
    assert env["OTEL_TRACES_EXPORTER"] == "otlp"
    assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" not in env
    assert "OTEL_EXPORTER_OTLP_TRACES_HEADERS" not in env
    assert "x-langfuse" not in env["OTEL_EXPORTER_OTLP_HEADERS"]


def test_logfire_token_does_not_implicitly_select_remote_export(tmp_path, monkeypatch):
    monkeypatch.delenv("A13N_DEV_TRACE_BACKEND", raising=False)
    monkeypatch.setenv("LOGFIRE_TOKEN", "fictional-write-token")
    env = trace_environment(local_langfuse(tmp_path))
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:3000/api/public/otel"
    assert "fictional-write-token" not in env["OTEL_EXPORTER_OTLP_HEADERS"]


def test_explicit_logfire_requires_token_unless_tracing_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("A13N_DEV_TRACE_BACKEND", "logfire")
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    langfuse = local_langfuse(tmp_path)
    with pytest.raises(ValueError, match="LOGFIRE_TOKEN"):
        trace_environment(langfuse)
    settings = langfuse.environment.settings
    settings = settings.model_copy(
        update={"observability": settings.observability.model_copy(update={"tracing": False})}
    )
    disabled = Langfuse(dataclasses.replace(langfuse.environment, settings=settings))
    assert trace_environment(disabled)["OTEL_TRACES_EXPORTER"] == "none"
