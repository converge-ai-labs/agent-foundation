from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import verify, verify_cache


@pytest.fixture
def repository(tmp_path: Path, monkeypatch):
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True).stdout.strip()

    git("init", "-q")
    for name in ("backend.py", "frontend.ts", "shared.json"):
        (tmp_path / name).write_text("original")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "initial")
    monkeypatch.setattr(verify, "REPOSITORY_ROOT", tmp_path)
    return tmp_path


def test_failure_does_not_discard_unaffected_successes(repository: Path, monkeypatch, capsys) -> None:
    record = repository / ".git/checks.json"
    steps = [
        verify.Step("backend", ["check", "backend"], cwd=repository, inputs=("backend.py", "shared.json")),
        verify.Step("frontend", ["check", "frontend"], cwd=repository, inputs=("frontend.ts", "shared.json")),
    ]
    calls = []
    fail_frontend = True
    real_run = subprocess.run

    def run(command, **kwargs):
        if command[0] != "check":
            return real_run(command, **kwargs)
        calls.append(command[1])
        return subprocess.CompletedProcess(command, int(fail_frontend and command[1] == "frontend"))

    monkeypatch.setattr(subprocess, "run", run)

    def verify_now() -> int:
        cache = verify_cache.Successes(repository, record, verify.snapshot())
        return verify.run(steps, dry_run=False, cache=cache)

    assert verify_now() == 1
    assert calls == ["backend", "frontend"]
    (repository / "frontend.ts").write_text("fixed")
    fail_frontend = False
    calls.clear()
    assert verify_now() == 0
    assert calls == ["frontend"]
    assert "backend: reusing successful check" in capsys.readouterr().out
    calls.clear()
    (repository / "shared.json").write_text("changed")
    assert verify_now() == 0
    assert calls == ["backend", "frontend"]
    calls.clear()
    monkeypatch.setenv("PYTEST_ADDOPTS", "--new-option")
    assert verify_now() == 0
    assert calls == ["backend", "frontend"]


def test_cache_keys_use_nodeids_and_detect_deletions_and_new_inputs(repository: Path, tmp_path: Path) -> None:
    cache = verify_cache.Successes(repository, repository / ".git/checks.json", verify.snapshot())
    listings = [tmp_path / "first.txt", tmp_path / "second.txt"]
    for listing in listings:
        listing.write_text("tests/test_example.py::test_value[a b]\n")

    def signature(cache, listing):
        return cache.signature(["make", "test", f"PYTHON_TEST_DIRS=@{listing}"], repository, ("backend.py",), None)

    first = signature(cache, listings[0])
    assert signature(cache, listings[1]) == first
    listings[1].write_text("tests/test_example.py::test_value[c]\n")
    assert signature(cache, listings[1]) != first
    (repository / "backend.py").unlink()
    changed = verify_cache.Successes(repository, repository / ".git/checks.json", verify.snapshot())
    assert signature(changed, listings[0]) != first


def test_dry_run_never_records_success(repository: Path) -> None:
    record = repository / ".git/checks.json"
    cache = verify_cache.Successes(repository, record, verify.snapshot())
    assert verify.run([verify.Step("unused", ["does-not-exist"])], dry_run=True, cache=cache) == 0
    assert not record.exists()


def test_declared_globs_include_new_resource_inputs(repository: Path) -> None:
    record = repository / ".git/checks.json"
    before = verify_cache.Successes(repository, record, verify.snapshot())
    inputs = ("config/*.json",)
    signature = before.signature(["check"], repository, inputs, None)
    (repository / "config").mkdir()
    (repository / "config/new.json").write_text("{}")
    after = verify_cache.Successes(repository, record, verify.snapshot())
    assert after.signature(["check"], repository, inputs, None) != signature
