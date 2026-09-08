from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.run_python_tests import main

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
        [sys.executable, "-m", "pytest", "-n", "2", "--dist", "loadgroup", ui],
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
        ["make", "--dry-run", "a13n-harness-ui-webui-check-all", "python-build"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("npm --prefix apps/a13n-harness-ui run build\n") == 1
    assert result.stdout.count("npm --prefix apps/a13n-harness-ui run check\n") == 1
    assert "npm --prefix apps/a13n-harness-ui run check:all" not in result.stdout
    assert "scripts/prepare-a13n-harness-ui-assets.py" in result.stdout
    assert "uv build --all-packages" in result.stdout
