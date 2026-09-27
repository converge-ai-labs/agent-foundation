from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from queue import Queue

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


@pytest.fixture
def verify_processes():
    """Real verification entry points with a controlled, inexpensive check body."""
    processes = []

    def start(root: Path, name: str, *args: str):
        script = """
import sys
from pathlib import Path
from scripts import verify, verify_cache

verify.REPOSITORY_ROOT = Path(sys.argv[1])
name = sys.argv[2]

def check(args, tree):
    records = verify_cache.Successes(verify.REPOSITORY_ROOT, verify._git_path('a13n-verify-checks'), tree)
    previous = ','.join(sorted(records.passed))
    records.save(name, name)
    print('entered', name, previous, flush=True)
    sys.stdin.readline()
    return 0

verify.verify_changes = check
raise SystemExit(verify.main(sys.argv[3:]))
"""
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(root), name, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "PYTHONPATH": str(Path(verify.__file__).resolve().parents[1])},
        )
        processes.append(process)
        output: Queue[str] = Queue()

        def read_output():
            assert process.stdout is not None
            for line in process.stdout:
                output.put(line.strip())

        threading.Thread(target=read_output, daemon=True).start()
        return process, output

    yield start
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()


def _release(process: subprocess.Popen[str]) -> None:
    assert process.stdin is not None
    process.stdin.write("\n")
    process.stdin.flush()
    assert process.wait(timeout=10) == 0


@pytest.mark.parametrize("no_cache", [False, True])
@pytest.mark.parametrize("terminate", [False, True])
def test_concurrent_verify_waits_then_reads_completed_records(repository: Path, verify_processes, no_cache, terminate):
    first, first_output = verify_processes(repository, "first")
    assert first_output.get(timeout=10) == "entered first"
    record = verify._git_path("a13n-verify-checks")
    second, second_output = verify_processes(repository, "second", *(["--no-cache"] if no_cache else []))
    assert second_output.get(timeout=10).startswith("waiting for another verify run in this worktree")
    # In particular, --no-cache must not clear a running process's results before locking.
    assert json.loads(record.read_text()) == {"first": "first"}
    if terminate:
        first.kill()
        first.wait(timeout=10)
    else:
        _release(first)
    assert second_output.get(timeout=10) == ("entered second" if no_cache else "entered second first")
    _release(second)
    assert json.loads(record.read_text()) == (
        {"second": "second"} if no_cache else {"first": "first", "second": "second"}
    )
    # The parent and child environments differ; inspect the completed baseline directly.
    tree, mode, _context = verify._git_path(verify.PASSED_RECORD).read_text().split()
    assert (tree, mode) == (verify.snapshot(), "changed")


def test_other_worktree_does_not_wait_for_active_verify(repository: Path, verify_processes):
    other = repository.parent / "other-worktree"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(other), "HEAD"], cwd=repository, capture_output=True, check=True
    )
    first, first_output = verify_processes(repository, "first")
    assert first_output.get(timeout=10) == "entered first"
    second, second_output = verify_processes(other, "second")
    assert second_output.get(timeout=10) == "entered second"
    assert first.poll() is None
    _release(second)
    _release(first)
    assert json.loads(verify._git_path("a13n-verify-checks").read_text()) == {"first": "first"}


def test_dry_run_does_not_acquire_worktree_lock(repository: Path, monkeypatch):
    def unexpected_lock(_path):
        pytest.fail("dry-run should not wait for the verification lock")

    monkeypatch.setattr(verify_cache, "worktree_lock", unexpected_lock)
    monkeypatch.setattr(verify, "plan", lambda *args, **kwargs: verify.Plan())
    assert verify.main(["--dry-run", "--no-cache", "README.md"]) == 0
