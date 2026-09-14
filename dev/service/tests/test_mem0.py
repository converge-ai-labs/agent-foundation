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
