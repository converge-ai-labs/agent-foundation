"""Notifications cover semantic/runtime changes without building downstream SDKs."""

import fnmatch
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/notify-service-contract.yml"


def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


@pytest.mark.parametrize(
    "changed",
    [
        "proto/a13n-service/openapi.json",
        "proto/a13n-service/notification-client.schema.json",
        "proto/a13n-service/run-stream-event.schema.json",
        "proto/a13n-service/fixtures/wire.json",
        "spec/api-conventions.md",
        "spec/a13n-service/21-native-streaming-and-notifications.md",
        "spec/a13n-service/20-agent-control-queued-submissions.md",
        "packages/a13n-service/a13n_service/gateway/native_streaming.py",
        "packages/a13n-service/a13n_service/gateway/notifications.py",
        "packages/a13n-service/a13n_service/run_stream/projector.py",
        "packages/a13n-harness/a13n_harness/stream.py",
        "packages/a13n-stream-protocol/a13n_stream_protocol/events.py",
        "uv.lock",
    ],
)
def test_client_relevant_changes_notify_even_without_openapi_changes(changed: str) -> None:
    assert any(fnmatch.fnmatchcase(changed, pattern) for pattern in workflow()[True]["push"]["paths"])


def test_notification_is_configured_main_only_and_target_scoped() -> None:
    definition = workflow()
    assert definition[True]["push"]["branches"] == ["main"]
    assert "pull_request" not in definition[True]
    assert definition["permissions"] == {"contents": "read"}
    job = definition["jobs"]["notify"]
    assert "refs/heads/main" in job["if"] and "SERVICE_CONTRACT_APP_CLIENT_ID != ''" in job["if"]
    assert job["strategy"]["fail-fast"] is False
    assert set(job["strategy"]["matrix"]["repository"]) == {
        f"a13n-sdk-{language}" for language in ("python", "go", "rust", "typescript")
    }
    app = next(step for step in job["steps"] if "create-github-app-token" in step.get("uses", ""))["with"]
    assert app["repositories"] == "${{ matrix.repository }}" and app["permission-contents"] == "write"
    assert app["owner"] == "converge-ai-labs"
    assert not any("sdk/" in step.get("run", "") or "make " in step.get("run", "") for step in job["steps"])


def test_notification_payload_uses_service_identity_and_no_sdk_execution(tmp_path: Path) -> None:
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGUMENTS"\ncat > "$PAYLOAD"\n')
    gh.chmod(0o755)
    sha = "a" * 40
    step = workflow()["jobs"]["notify"]["steps"][-1]
    subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        check=True,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "SOURCE_SHA": sha,
            "GITHUB_SHA": "b" * 40,
            "TARGET_REPOSITORY": "a13n-sdk-rust",
            "ARGUMENTS": str(tmp_path / "args"),
            "PAYLOAD": str(tmp_path / "payload"),
        },
    )
    assert (tmp_path / "args").read_text().splitlines() == [
        "api",
        "--method",
        "POST",
        "repos/converge-ai-labs/a13n-sdk-rust/dispatches",
        "--input",
        "-",
    ]
    assert json.loads((tmp_path / "payload").read_text()) == {
        "event_type": "service-contract-updated",
        "client_payload": {"repository": "converge-ai-labs/agent-foundation", "commit": sha},
    }


def test_source_validation_accepts_only_full_main_line_commits(tmp_path: Path) -> None:
    def git(*arguments: str) -> str:
        return subprocess.check_output(["git", "-C", str(tmp_path), *arguments], text=True).strip()

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("commit", "--allow-empty", "-m", "Main")
    main = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", main)
    git("switch", "-c", "unmerged")
    git("commit", "--allow-empty", "-m", "Unmerged")
    unmerged = git("rev-parse", "HEAD")
    step = next(step for step in workflow()["jobs"]["notify"]["steps"] if step.get("id") == "source")
    for sha, success in [
        (main, True),
        (unmerged, False),
        (main[:12], False),
        ("main", False),
        ("$(echo unsafe)", False),
    ]:
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", step["run"]],
            cwd=tmp_path,
            env={**os.environ, "SOURCE_SHA": sha, "GITHUB_OUTPUT": str(tmp_path / "output")},
            capture_output=True,
            text=True,
        )
        assert (result.returncode == 0) is success, result.stderr
    assert (tmp_path / "output").read_text() == f"sha={main}\n"
