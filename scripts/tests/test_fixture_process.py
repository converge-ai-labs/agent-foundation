"""Exercise real local HTTP fixtures, listener ownership and process cleanup."""

import socket
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx2
import pytest
from fastapi.testclient import TestClient

from dev.fixtures import model
from dev.fixtures import process as fixtures


@pytest.fixture
def started_processes(monkeypatch):
    processes = []
    popen = subprocess.Popen

    def start(*args, **kwargs):
        child = popen(*args, **kwargs)
        processes.append(child)
        return child

    monkeypatch.setattr(fixtures.subprocess, "Popen", start)
    return processes


@pytest.mark.parametrize("module", ["model", "scripted_model", "mem0", "mcp", "oauth", "composio"])
def test_fixture_serves_owned_listener_and_exits(module, tmp_path, started_processes):
    arguments = (
        ("--database", str(tmp_path / "fixture database.sqlite")) if module in {"mcp", "oauth", "composio"} else ()
    )
    with fixtures.fixture_process(f"dev.fixtures.{module}", arguments=arguments) as url:
        with httpx2.Client(trust_env=False, timeout=2) as client:
            assert client.get(url + "/healthz").status_code == 200
        assert len(started_processes) == 1
        assert started_processes[0].poll() is None
        port = urlsplit(url).port
    assert started_processes[0].poll() is not None
    # Windows can finish TCP teardown shortly after both owners have closed.
    deadline = time.monotonic() + 5
    while True:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                break
        assert time.monotonic() < deadline, "The fixture listener remained open"
        time.sleep(0.05)


def test_fixture_does_not_reuse_an_occupied_port(started_processes):
    with socket.socket() as owner:
        owner.bind(("127.0.0.1", 0))
        owner.listen()
        with pytest.raises(OSError):
            with fixtures.fixture_process("dev.fixtures.model", port=owner.getsockname()[1]):
                pytest.fail("An occupied port must not be reused")
        assert started_processes == []


def test_fixture_exits_when_context_body_fails(started_processes):
    with pytest.raises(ValueError, match="body failed"):
        with fixtures.fixture_process("dev.fixtures.model"):
            raise ValueError("body failed")
    assert len(started_processes) == 1
    assert started_processes[0].poll() is not None


def test_fixture_reports_early_exit_and_reaps_child(started_processes):
    with pytest.raises(RuntimeError, match="exited during startup"):
        with fixtures.fixture_process("dev.fixtures.model", arguments=("--invalid-option",)):
            pytest.fail("The invalid fixture must not become ready")
    assert len(started_processes) == 1
    assert started_processes[0].poll() is not None


def test_fixture_reaps_child_when_readiness_times_out(monkeypatch, started_processes):
    clock = iter((0, 31))
    monkeypatch.setattr(fixtures, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _: None))

    def not_ready(*args, **kwargs):
        raise httpx2.ConnectError("not ready")

    monkeypatch.setattr(fixtures.httpx2.Client, "get", not_ready)
    with pytest.raises(RuntimeError, match="did not become ready"):
        with fixtures.fixture_process("dev.fixtures.model"):
            pytest.fail("The unready fixture must not be yielded")
    assert len(started_processes) == 1
    assert started_processes[0].poll() is not None


def test_model_response_uses_utf8_with_a_legacy_default_encoding(monkeypatch):
    read_text = Path.read_text

    def legacy_read(path, encoding=None, errors=None, newline=None):
        return read_text(path, encoding=encoding or "gbk", errors=errors, newline=newline)

    monkeypatch.setattr(Path, "read_text", legacy_read)
    with TestClient(model.app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "local-scripted", "messages": [{"role": "user", "content": "[long]"}]},
        )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == read_text(
        Path(model.__file__).with_name("response.md"), encoding="utf-8"
    )
