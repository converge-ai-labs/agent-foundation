"""Service E2E support checks that run without Docker."""

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
from e2e.service.stack import LEASE_SECONDS, Stores, write_config

ROOT = Path(__file__).resolve().parents[2]


def collect_scenarios(*arguments: str) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-m", "e2e.service", "--collect-only", "-qq", *arguments],
        cwd=ROOT,
        # Parent runners can change verbosity; this subprocess needs one node ID per line.
        env={**os.environ, "PYTEST_ADDOPTS": ""},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {line for line in result.stdout.splitlines() if "::" in line}


def test_the_e2e_suite_collects_every_journey_module():
    collected = {node.split("::")[0] for node in collect_scenarios()}
    assert collected == {path.relative_to(ROOT).as_posix() for path in (ROOT / "e2e/service").glob("test_*.py")}


def test_hosted_e2e_journeys_run_only_when_asked_for():
    environments = "e2e/service/test_environments.py"
    lifecycle = f"{environments}::test_a_run_uses_its_environment_across_its_lifecycle"
    default = collect_scenarios()
    assert f"{lifecycle}[docker]" in default
    assert not {node for node in default if "e2b" in node or "modal" in node}
    assert f"{lifecycle}[modal]" in collect_scenarios("--hosted")
    assert f"{lifecycle}[modal]" in collect_scenarios("-m", "hosted")
    assert collect_scenarios("-k", "e2b") == {
        f"{lifecycle}[e2b]",
        f"{environments}::test_a_ready_e2b_sandbox_is_renewed_past_its_timeout",
    }
    # A selection that names no vendor type never pulls billable journeys in.
    assert collect_scenarios("-k", "environment") == {f"{lifecycle}[local]", f"{lifecycle}[docker]"}


@pytest.mark.parametrize("worker_slots", [1, 4])
def test_the_e2e_service_configuration_is_valid(tmp_path, monkeypatch, worker_slots):
    for name in [name for name in os.environ if name.startswith("A13N_")]:
        monkeypatch.delenv(name)
    stores = Stores(
        postgres_url="postgresql+psycopg://e2e:fixture@127.0.0.1:5432/postgres",
        template="e2e_template",
        redis_url="redis://127.0.0.1:6379/0",
        certificate=tmp_path / "certificate.pem",
        key=tmp_path / "key.pem",
        encryption_key=base64.b64encode(bytes(32)).decode(),
        tenant={},
    )
    journey = load_settings(
        write_config(tmp_path / "journey.toml", stores, "e2e_journey", tmp_path / "objects", worker_slots=worker_slots)
    )
    assert journey.worker.slots == worker_slots
    assert journey.worker.lease_seconds == LEASE_SECONDS and journey.server.tls_certificate == stores.certificate


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
