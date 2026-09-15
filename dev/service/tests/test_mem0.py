"""Local OSS lifecycle follows selected configuration, not ambient Compose settings."""

import os
import subprocess
from types import SimpleNamespace

import pytest
from a13n_service.configuration.sections import MemorySettings

from dev.service.mem0 import Mem0
from dev.service.tests.test_commands import local_environment


@pytest.mark.parametrize(
    "memory",
    [
        {"provider": "oss"},
        {"provider": "oss", "api_key": "test"},
        {"provider": "platform"},
        {"provider": "oss", "api_key": "test", "base_url": "https://user:key@example.com"},
    ],
)
def test_enabled_configuration_requires_native_endpoint_and_key(memory):
    with pytest.raises(ValueError):
        MemorySettings(**memory)


def test_compose_uses_checkout_and_selected_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("MEM0_LOCAL_PORT", "9999")
    monkeypatch.setenv("MEM0_LOCAL_API_KEY", "ambient")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="ready")

    monkeypatch.setattr(subprocess, "run", run)
    memory = Mem0(local_environment(tmp_path))
    assert memory.compose("ps", capture=True) == "ready"
    command, options = calls[0]
    assert command[command.index("--env-file") + 1] == os.devnull
    assert command[command.index("--project-name") + 1] == memory.environment.project + "-mem0"
    assert options["env"]["MEM0_LOCAL_PORT"] == "18888"
    assert options["env"]["MEM0_LOCAL_API_KEY"] == "local-mem0-api-key"


@pytest.mark.parametrize("port", [8000, 18080, 15432, 3000])
def test_port_conflicts_fail_before_compose(tmp_path, port):
    memory = Mem0(local_environment(tmp_path, memory={"base_url": f"http://127.0.0.1:{port}"}))
    with pytest.raises(ValueError, match="overlaps"):
        memory.validate()


def test_embedding_size_and_real_endpoint_configuration_are_explicit(tmp_path, monkeypatch):
    memory = Mem0(local_environment(tmp_path))
    monkeypatch.setenv("MEM0_OSS_EMBEDDING_DIMENSIONS", "1536")
    with pytest.raises(ValueError, match="actual size"):
        memory.validate()
    monkeypatch.setenv("MEM0_OSS_EMBEDDING_BASE_URL", "https://example.com/v1")
    monkeypatch.delenv("MEM0_OSS_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("MEM0_OSS_EMBEDDING_API_KEY", raising=False)
    with pytest.raises(ValueError, match="custom embedding"):
        memory.validate()
    monkeypatch.setenv("MEM0_OSS_EMBEDDING_MODEL", "real-model")
    monkeypatch.setenv("MEM0_OSS_EMBEDDING_API_KEY", "test-key")
    memory.validate()


@pytest.mark.parametrize(
    "config",
    [
        {"provider": "none"},
        {"provider": "platform", "api_key": "test"},
        {"provider": "oss", "base_url": "https://memory.example", "api_key": "test"},
    ],
)
def test_external_backends_are_not_managed(tmp_path, monkeypatch, config):
    memory = Mem0(local_environment(tmp_path, memory=config))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("must not start Docker"))
    assert not memory.enabled
    memory.start()


def test_local_configuration_uses_only_native_api_and_separate_model_keys():
    import json

    import httpx2

    from dev.mem0.configuration import configure

    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        assert request.headers["X-API-Key"] == "local-admin"
        if request.url.path == "/configure":
            config = json.loads(request.content)
            assert config["embedder"]["config"] == {
                "model": "real-embedding",
                "api_key": "embedding-only",
                "openai_base_url": "https://embedding.example/v1",
                "embedding_dims": 1536,
            }
            assert config["llm"]["config"]["api_key"] == "llm-only"
            assert config["vector_store"]["config"]["embedding_model_dims"] == 1536
            assert config["vector_store"]["config"]["collection_name"] == "new_model_collection"
            return httpx2.Response(200, json={"message": "configured"})
        assert request.url.path == "/memories" and request.url.params["top_k"] == "1"
        return httpx2.Response(200, json={"results": []})

    with httpx2.Client(
        base_url="http://local/", headers={"X-API-Key": "local-admin"}, transport=httpx2.MockTransport(handle)
    ) as client:
        configure(
            client,
            {
                "MEM0_OSS_EMBEDDING_BASE_URL": "https://embedding.example/v1",
                "MEM0_OSS_EMBEDDING_MODEL": "real-embedding",
                "MEM0_OSS_EMBEDDING_API_KEY": "embedding-only",
                "MEM0_OSS_EMBEDDING_DIMENSIONS": "1536",
                "MEM0_OSS_EMBEDDING_SEND_DIMENSIONS": "true",
                "MEM0_OSS_LLM_API_KEY": "llm-only",
                "MEM0_OSS_COLLECTION": "new_model_collection",
            },
        )
    assert calls == [("POST", "/configure"), ("GET", "/memories")]


def test_dev_image_never_changes_upstream_source_or_adds_routes():
    from dev.service.environment import ROOT

    dockerfile = (ROOT / "dev/mem0/Dockerfile").read_text()
    assert "patch " not in dockerfile and "COPY " not in dockerfile
    assert "apply_overlay" not in dockerfile
    assert not list((ROOT / "dev/mem0").glob("**/*.patch"))
