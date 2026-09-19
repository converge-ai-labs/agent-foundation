from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.run_python_tests import default_workers, main

REPOSITORY_ROOT = Path(__file__).parents[2]
SERVICE = "packages/a13n-service/tests"
UI = "packages/a13n-harness-ui/tests"


@pytest.fixture
def test_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    for root in (SERVICE, UI, "scripts/tests"):
        directory = tmp_path / root
        directory.mkdir(parents=True)
        for name in ("test_one.py", "test_two.py"):
            (directory / name).touch()
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []

    def run(command, *, check):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("scripts.run_python_tests.subprocess.run", run)
    return calls


def test_selected_paths_share_one_process_per_package_in_selection_order(test_workspace) -> None:
    first = f"{SERVICE}/test_one.py::test_example"
    second = f"{SERVICE}/test_two.py"
    ui = f"{UI}/test_one.py"
    assert main([first, ui, second]) == 0
    assert test_workspace == [
        [sys.executable, "-m", "pytest", "-n", "7", "--dist", "loadgroup", first, second],
        [sys.executable, "-m", "pytest", "-n", str(default_workers(Path(UI))), "--dist", "loadgroup", ui],
    ]


def test_default_selection_keeps_all_packages_and_accepts_zero_workers(test_workspace) -> None:
    assert main(["--workers", "0"]) == 0
    assert [command[-1] for command in test_workspace] == [UI, SERVICE, "scripts/tests"]
    assert all(command[4] == "0" for command in test_workspace)


@pytest.mark.parametrize("invalid", ["missing.py", "packages", f"{SERVICE}/missing.py"])
def test_invalid_selection_fails_before_starting_any_suite(test_workspace, invalid) -> None:
    with pytest.raises(SystemExit) as error:
        main([SERVICE, invalid])
    assert error.value.code == 2
    assert test_workspace == []


def test_failed_suite_preserves_exit_code_and_stops_later_packages(test_workspace, monkeypatch) -> None:
    def fail(command, *, check):
        test_workspace.append(command)
        return subprocess.CompletedProcess(command, 5)

    monkeypatch.setattr("scripts.run_python_tests.subprocess.run", fail)
    assert main([SERVICE, UI]) == 5
    assert len(test_workspace) == 1


def test_pytest_groups_by_file_and_preserves_explicit_cross_file_groups(tmp_path: Path) -> None:
    shutil.copy2(REPOSITORY_ROOT / "conftest.py", tmp_path / "conftest.py")
    with (tmp_path / "conftest.py").open("a") as config:
        config.write(
            "\nimport json\n"
            "def pytest_collection_finish(session):\n"
            "    groups = {item.nodeid: [mark.args[0] for mark in item.iter_markers('xdist_group')] "
            "for item in session.items}\n"
            "    Path('groups.json').write_text(json.dumps(groups))\n"
        )
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers = xdist_group(name): worker group\n")
    for name in ("one", "two"):
        (tmp_path / f"test_{name}.py").write_text(
            "import pytest\n"
            "def test_default(): pass\n"
            "def test_same_file(): pass\n"
            "@pytest.mark.xdist_group('shared')\n"
            "def test_shared(): pass\n"
        )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=tmp_path,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    groups = json.loads((tmp_path / "groups.json").read_text())
    assert len(groups) == 6
    for name in ("one", "two"):
        assert groups[f"test_{name}.py::test_default"] == [f"test_{name}.py"]
        assert groups[f"test_{name}.py::test_same_file"] == [f"test_{name}.py"]
        assert groups[f"test_{name}.py::test_shared"] == ["shared"]


def test_full_frontend_check_and_python_packaging_share_one_build() -> None:
    result = subprocess.run(
        ["make", "--dry-run", "frontend-check-all", "python-build"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("pnpm --dir frontend --filter a13n-harness-ui-webui run build\n") == 1
    assert result.stdout.count("pnpm --dir frontend run check\n") == 1
    assert result.stdout.count("pnpm --dir frontend run test\n") == 1
    assert result.stdout.count("pnpm --dir frontend --filter a13n-ui run build\n") == 1
    assert result.stdout.count("pnpm --dir frontend --filter a13n-console run build\n") == 1
    assert result.stdout.count("pnpm --dir frontend install --frozen-lockfile\n") == 1
    assert "scripts/prepare-a13n-harness-ui-assets.py" in result.stdout
    assert "uv build --all-packages" in result.stdout


@pytest.mark.parametrize("component", ["a13n-logging", "a13n-service"])
def test_logging_and_service_build_targets_publish_only_their_own_package(component: str) -> None:
    result = subprocess.run(
        ["make", "--dry-run", f"{component}-python-build"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    build_commands = [line for line in result.stdout.splitlines() if "uv build" in line]
    assert build_commands == [f"uv build --package {component} --out-dir dist"]


@pytest.mark.parametrize(
    "path,regenerates",
    [
        ("packages/a13n-service/tests/test_openapi.py", False),
        ("packages/a13n-harness/tests/test_agent.py", False),
        ("packages/a13n-service/src/a13n_service/app.py", True),
        ("packages/a13n-service/pyproject.toml", True),
        ("scripts/export-a13n-service-openapi.py", True),
        ("uv.lock", True),
    ],
)
def test_service_contract_hook_skips_package_tests_but_keeps_contract_inputs(path: str, regenerates: bool) -> None:
    config = yaml.safe_load((REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text())
    hook = next(hook for repo in config["repos"] for hook in repo["hooks"] if hook["id"] == "service-contract-generate")
    selected = bool(re.search(hook["files"], path)) and not re.search(hook.get("exclude", "^$"), path)
    assert bool(selected) is regenerates


def test_markdown_hook_uses_locked_workspace_formatter() -> None:
    config = yaml.safe_load((REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text())
    hooks = [(repo, hook) for repo in config["repos"] for hook in repo["hooks"] if hook["id"] == "mdformat"]
    assert len(hooks) == 1
    repo, hook = hooks[0]
    assert repo["repo"] == "local"
    assert hook["language"] == "system"
    assert hook["entry"] == "uv run --locked mdformat"
    assert hook["types"] == ["markdown"]
    assert hook["args"] == ["--number"]
    assert hook.get("pass_filenames", True) is True
    assert "additional_dependencies" not in hook


def test_automation_ci_covers_markdown_hook_configuration() -> None:
    workflow = yaml.safe_load((REPOSITORY_ROOT / ".github/workflows/ci-automation.yml").read_text())
    for event in ("pull_request", "push"):
        patterns = workflow[True][event]["paths"]
        for path in (".pre-commit-config.yaml", ".mdformat.toml"):
            assert any(Path(path).full_match(pattern) for pattern in patterns), (event, path)


@pytest.mark.parametrize("target", ["install", "format", "check", "check-all", "build", "clean"])
def test_root_targets_do_not_require_sdk_checkouts(tmp_path: Path, target: str) -> None:
    shutil.copy2(REPOSITORY_ROOT / "Makefile", tmp_path / "Makefile")
    result = subprocess.run(["make", "--dry-run", target], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "sdk/" not in result.stdout
    assert "sdk-" not in result.stdout
    assert "a13n-service-cli" not in result.stdout
    assert not (tmp_path / "sdk").exists()


def test_service_ci_owns_export_drift_and_tests() -> None:
    workflow = yaml.safe_load((REPOSITORY_ROOT / ".github/workflows/ci-a13n-service.yml").read_text())
    for event in ("pull_request", "push"):
        patterns = workflow[True][event]["paths"]
        for path in (
            "proto/a13n-service/openapi.json",
            "proto/a13n-service/run-stream-event.schema.json",
            "scripts/export-a13n-service-openapi.py",
            "scripts/tests/test_service_contract.py",
            "packages/a13n-service/a13n_service/gateway/router.py",
            "packages/a13n-harness/a13n_harness/types.py",
            "packages/a13n-stream-protocol/a13n_stream_protocol/events.py",
            "uv.lock",
        ):
            assert any(Path(path).full_match(pattern) for pattern in patterns), (event, path)
    steps = workflow["jobs"]["validation"]["steps"]
    for command in (
        "uv run --locked python scripts/export-a13n-service-openapi.py --check",
        "uv run --locked python -m pytest scripts/tests/test_service_contract.py",
    ):
        step = next(step for step in steps if step.get("run") == command)
        assert step["if"] == "matrix.name == 'checks'"
    assert workflow["jobs"]["python"]["needs"] == "validation"
