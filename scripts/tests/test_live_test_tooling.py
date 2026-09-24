"""Live-journey support without Docker, and shared tracing infrastructure."""

import base64
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest
from a13n_service.settings import load_settings

from dev.fixtures.process import fixture_process
from dev.live_tests.console import service_config
from dev.live_tests.stack import LEASE_SECONDS, Stores, write_config
from dev.observability import langfuse

ROOT = Path(__file__).resolve().parents[2]


def test_the_live_suite_collects_every_journey_module():
    result = subprocess.run(
        [sys.executable, "-m", "dev.live_tests", "--collect-only", "-qq"], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    collected = {line.split("::")[0] for line in result.stdout.splitlines() if "::" in line}
    assert collected == {path.relative_to(ROOT).as_posix() for path in (ROOT / "dev/live_tests").glob("test_*.py")}


def test_hosted_live_journeys_run_only_when_asked_for():
    def collected(*arguments: str) -> set[str]:
        command = [sys.executable, "-m", "dev.live_tests", "--collect-only", "-qq", *arguments]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        return {line.split("::")[1] for line in result.stdout.splitlines() if "::" in line}

    default = collected()
    assert "test_a_run_uses_its_environment_across_its_lifecycle[docker]" in default
    assert not {journey for journey in default if "e2b" in journey or "modal" in journey}
    assert "test_a_run_uses_its_environment_across_its_lifecycle[modal]" in collected("--hosted")
    assert "test_a_run_uses_its_environment_across_its_lifecycle[modal]" in collected("-m", "hosted")
    assert collected("-k", "e2b") == {
        "test_a_run_uses_its_environment_across_its_lifecycle[e2b]",
        "test_a_ready_e2b_sandbox_is_renewed_past_its_timeout",
    }
    # A selection that names no vendor type never pulls billable journeys in.
    assert collected("-k", "environment") == {
        "test_a_run_uses_its_environment_across_its_lifecycle[local]",
        "test_a_run_uses_its_environment_across_its_lifecycle[docker]",
    }


def test_the_live_service_configurations_are_valid(tmp_path, monkeypatch):
    for name in [name for name in os.environ if name.startswith("A13N_")]:
        monkeypatch.delenv(name)
    stores = Stores(
        postgres_url="postgresql+psycopg://live:fixture@127.0.0.1:5432/postgres",
        template="live_template",
        redis_url="redis://127.0.0.1:6379/0",
        certificate=tmp_path / "certificate.pem",
        key=tmp_path / "key.pem",
        encryption_key=base64.b64encode(bytes(32)).decode(),
        tenant={},
    )
    journey = load_settings(write_config(tmp_path / "journey.toml", stores, "live_journey", tmp_path / "objects"))
    assert journey.worker.lease_seconds == LEASE_SECONDS and journey.server.tls_certificate == stores.certificate
    console = tmp_path / "console.toml"
    console.write_text(
        service_config(
            tmp_path,
            "postgresql+psycopg://live:fixture@127.0.0.1:5432/console",
            stores.redis_url,
            console="http://localhost:5173",
            workspace_id="ws_console",
            origins=("http://127.0.0.1:18080",),
        )
    )
    assert load_settings(console).server.public_url == "http://localhost:5173"


def wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 5
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.05)


def test_the_scripted_model_answers_holds_and_records_requests():
    with fixture_process("dev.fixtures.scripted_model") as url, httpx2.Client(base_url=url, trust_env=False) as client:

        def script(**turn: object) -> None:
            client.post("/fixture/turns", json=turn).raise_for_status()

        def ask(text: str, **options: float) -> list[dict]:
            """The streamed deltas that carry content or tool calls."""
            body = {"model": "scripted", "stream": True, "messages": [{"role": "user", "content": text}]}
            response = client.post("/v1/chat/completions", json=body, **options)
            chunks = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
            deltas = [chunk["choices"][0]["delta"] for chunk in chunks if chunk["choices"]]
            return [delta for delta in deltas if delta.get("content") or delta.get("tool_calls")]

        def statuses(marker: str) -> list[str]:
            items = client.get("/fixture/requests", params={"marker": marker}).json()["items"]
            return [item["status"] for item in items]

        script(to="[text]", text="Hello there", chunks=3)
        script(to="[again]", text="Again", repeat=True)
        script(to="[tool]", tool_calls=[{"id": "call_1", "name": "lookup", "arguments": {"q": 1}}], hold="tool")
        script(to="[never]", text="Never", hold="never")
        assert [delta["content"] for delta in ask("[text] Hi")] == ["Hell", "o th", "ere"]
        assert [delta["content"] for delta in ask("[again] One") + ask("[again] Two")] == ["Again", "Again"]

        # A held turn answers once its gate opens.
        answers: list[list[dict]] = []
        waiting = threading.Thread(target=lambda: answers.append(ask("[tool] Look")))
        waiting.start()
        wait_until(lambda: statuses("[tool]") == ["held"])
        client.post("/fixture/gates/tool").raise_for_status()
        waiting.join(5)
        assert answers[0][0]["tool_calls"][0]["id"] == "call_1" and statuses("[tool]") == ["answered"]

        # A client that leaves while its turn is held is recorded as abandoned.
        with pytest.raises(httpx2.ReadTimeout):
            ask("[never] Wait", timeout=0.5)
        wait_until(lambda: statuses("[never]") == ["abandoned"])
        assert statuses("[text]") == ["answered"] and statuses("[again]") == ["answered", "answered"]


def test_shared_langfuse_reuses_verified_manifest_and_preserves_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(langfuse, "_machine_directory", lambda: tmp_path)
    stack = langfuse.Langfuse()
    monkeypatch.setattr(stack, "_project_resources", lambda: ())
    compose = stack._shared_compose(initialize=True, require_compatible_source=True)
    assert compose is not None
    manifest = json.loads((tmp_path / "langfuse-v2.json").read_text())
    assert manifest["project"] == "agent-foundation-local-langfuse-v2"
    assert stack._shared_compose(initialize=False, require_compatible_source=False) == compose
    calls = []
    monkeypatch.setattr(langfuse.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(stack, "_compose", lambda path, *args: calls.append(args))
    stack.stop()
    assert calls == [("down", "--remove-orphans")]
    assert compose.exists() and (tmp_path / "langfuse-v2.json").exists()
    compose.write_text("changed")
    with pytest.raises(ValueError, match="changed unexpectedly"):
        stack._shared_compose(initialize=False, require_compatible_source=False)


def test_shared_langfuse_uses_public_fixture_configuration(monkeypatch, tmp_path):
    seen = {}

    def run(command, **kwargs):
        seen.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(langfuse.subprocess, "run", run)
    langfuse.Langfuse()._compose(tmp_path / "compose.yaml", "up", "-d")
    assert seen["env"]["LANGFUSE_LOCAL_PUBLIC_KEY"] == langfuse.PUBLIC_KEY
    assert seen["env"]["LANGFUSE_LOCAL_SECRET_KEY"] == langfuse.SECRET_KEY
    assert "--env-file" in seen["command"]
    assert seen["env"]["LANGFUSE_LOCAL_PORT"] == "3000"
