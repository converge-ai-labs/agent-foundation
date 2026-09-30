"""The deployment smoke run always removes its disposable stack and never passes credentials as arguments."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deploy_smoke


@pytest.mark.parametrize("image_source", ["default", "environment", "make"])
def test_compose_smoke_uses_the_build_image_for_both_stacks(tmp_path, monkeypatch, image_source) -> None:
    image = "a13n-service:local" if image_source == "default" else "a13n-service:smoke-test"
    python = tmp_path / "python3"
    python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "print(json.dumps([sys.argv[1:], os.environ.get('A13N_SERVICE_IMAGE')]))\n"
    )
    python.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    for name in ("MAKEFLAGS", "MFLAGS", "MAKEFILES", "A13N_SERVICE_IMAGE"):
        monkeypatch.delenv(name, raising=False)
    if image_source == "environment":
        monkeypatch.setenv("A13N_SERVICE_IMAGE", image)
    arguments = [f"A13N_SERVICE_IMAGE={image}"] if image_source == "make" else []
    result = subprocess.run(
        ["make", "--no-print-directory", "compose-smoke", *arguments],
        cwd=deploy_smoke.ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    assert [json.loads(line) for line in result.stdout.splitlines()] == [
        [["scripts/deploy_smoke.py", stack], image] for stack in ("compose", "quickstart")
    ]


@pytest.mark.parametrize("failing", [None, "restarted", "defaults_changed"])
def test_compose_smoke_checks_both_starts_and_always_removes_the_stack(monkeypatch, failing) -> None:
    commands: list[tuple[str, ...]] = []
    checks: list[tuple[str, bool]] = []

    def compose(*args: str, capture: bool = False) -> str:
        commands.append(args)
        return "digest  /app/var/encryption.key\n" if capture else ""

    def check(base_url: str, email: str, password: str, *, first_run: bool) -> deploy_smoke.Browser:
        checks.append((base_url, first_run))
        if failing == "restarted" and not first_run:
            raise RuntimeError("sign-in failed")
        provider = {"id": "envp_docker", "type": "docker", "config": {}, "created_by_id": None}
        template = {
            "id": "envt_recreated" if failing == "defaults_changed" and not first_run else "envt_linux",
            "provider_id": provider["id"],
            "created_by_id": None,
            "config": {
                "recipe": {
                    "image": "ghcr.io/converge-ai-labs/a13n-docker-environment:dev",
                    "pull_policy": "if_missing",
                }
            },
        }
        browser = Mock(spec=deploy_smoke.Browser)
        browser.expect.side_effect = [{"items": [provider]}, {"items": [template]}]
        return browser

    monkeypatch.setattr(deploy_smoke, "compose", compose)
    monkeypatch.setattr(deploy_smoke, "check", check)
    # compose_smoke exports the port for docker compose; owning the variable here removes it after the test, so
    # later tests in this process do not load it as a Service setting.
    monkeypatch.setenv("A13N_PORT", "18123")
    monkeypatch.delenv("A13N_DOCKER_ENVIRONMENT_IMAGE", raising=False)
    if failing:
        error = RuntimeError if failing == "restarted" else AssertionError
        with pytest.raises(error):
            deploy_smoke.compose_smoke("18123")
        assert ("logs", "--no-color", "--tail", "200", "service") in commands
    else:
        deploy_smoke.compose_smoke("18123")
    assert checks == [("http://localhost:18123", True), ("http://127.0.0.1:18123", False)]
    assert commands[0] == ("up", "--detach", "--wait") and commands[-1] == ("down", "--volumes")
    assert ("restart", "service") in commands


def test_failed_expectations_report_the_error_but_not_the_request() -> None:
    browser = deploy_smoke.Browser("http://127.0.0.1:1")
    browser.request = lambda *_: (401, "application/json", {"error": {"code": "unauthenticated"}})  # type: ignore[method-assign]
    with pytest.raises(RuntimeError) as failure:
        browser.expect(200, "POST", "/api/v1/auth/login", {"email": "a@example.com", "password": "not-in-errors"})
    assert "unauthenticated" in str(failure.value) and "not-in-errors" not in str(failure.value)
