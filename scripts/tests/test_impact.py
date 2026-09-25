from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tia import astmap

from scripts import impact, verify

SOURCE = '''"""Models."""

LIMIT = 1


def value():
    return LIMIT


def other():
    return 2
'''
TESTS = """from a13n_core import models


def test_value():
    assert models.value() == 1


def test_other():
    assert models.other() == 2
"""
SETTINGS_SOURCE = """class Settings:
    limit = 1

    def current(self):
        return self.limit
"""
MODELS = "packages/a13n-core/a13n_core/models.py"
SETTINGS = "packages/a13n-core/a13n_core/settings.py"
UNMEASURED = "packages/a13n-core/a13n_core/unmeasured.py"
TEST_FILE = "packages/a13n-core/tests/test_models.py"
PACKAGE = "a13n-core"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A committed miniature repository with a recorded map: two mapped tests, two unrelated ones."""
    for relative, content in {
        MODELS: SOURCE,
        SETTINGS: SETTINGS_SOURCE,
        UNMEASURED: "X = 1\n",
        TEST_FILE: TESTS,
    }.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@example.com", "add", ".")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "base")
    monkeypatch.setattr(impact, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(verify, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(verify, "PACKAGES", tmp_path / "packages")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path.parent / f"{tmp_path.name}-cache"))
    document = {
        "version": 5,
        "ref": _git(tmp_path, "rev-parse", "HEAD"),
        "tests": {
            f"{TEST_FILE}::test_value": {MODELS: ["value"], TEST_FILE: ["test_value"]},
            f"{TEST_FILE}::test_other": {MODELS: ["other"], TEST_FILE: ["test_other"]},
            f"{TEST_FILE}::test_settings": {SETTINGS: ["Settings.current"]},
            f"{TEST_FILE}::test_unrelated_a": {},
            f"{TEST_FILE}::test_unrelated_b": {},
        },
        "reads": {},
        "funcmaps": {
            relative: {str(n): q for n, q in astmap.line_to_qualname_from_file(str(tmp_path / relative)).items()}
            for relative in (MODELS, SETTINGS, TEST_FILE)
        },
        "dynamic": {},
        "durations": {
            f"{TEST_FILE}::test_value": 0.5,
            f"{TEST_FILE}::test_other": 1.5,
            f"{TEST_FILE}::test_settings": 1,
        },
    }
    target = impact.map_path(PACKAGE, document["ref"])
    target.parent.mkdir(parents=True)
    with gzip.open(target, "wt") as handle:
        json.dump(document, handle)
    return tmp_path


def _select(root: Path, *files: str) -> impact.Selection:
    found = impact.find_map(PACKAGE)
    assert found is not None
    return impact.select(found, files or (MODELS,))


def test_find_map_walks_ancestors_and_reports_distance(repo: Path) -> None:
    assert impact.find_map("a13n-missing") is None
    found = impact.find_map(PACKAGE)
    assert found is not None and found.distance == 0 and len(found.tests) == 5
    (repo / "note.txt").write_text("later\n")
    _git(repo, "add", "note.txt")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "later")
    found = impact.find_map(PACKAGE)
    assert found is not None and found.distance == 1


def test_find_map_falls_back_to_a_recorded_commit_outside_the_history(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "note.txt").write_text("side\n")
    _git(repo, "add", "note.txt")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "side")
    recorded = _git(repo, "rev-parse", "HEAD")
    _git(repo, "reset", "-q", "--hard", base)  # the recorded commit exists but is no longer an ancestor
    with gzip.open(impact.map_path(PACKAGE, base), "rt") as handle:
        document = json.load(handle)
    impact.map_path(PACKAGE, base).unlink()
    with gzip.open(impact.map_path(PACKAGE, recorded), "wt") as handle:
        json.dump({**document, "ref": recorded}, handle)
    found = impact.find_map(PACKAGE)
    assert found is not None and found.distance == -1 and found.commit == recorded
    (repo / MODELS).write_text(SOURCE.replace("    return LIMIT", "    return LIMIT + 0"))
    assert _select(repo).tests == {f"{TEST_FILE}::test_value"}


def test_function_body_change_selects_only_its_callers(repo: Path) -> None:
    (repo / MODELS).write_text(SOURCE.replace("    return LIMIT", "    return LIMIT + 0"))
    assert _select(repo).tests == {f"{TEST_FILE}::test_value"}


def test_module_level_edit_or_insertion_selects_every_test_touching_the_file(repo: Path) -> None:
    (repo / MODELS).write_text(SOURCE.replace("LIMIT = 1", "LIMIT = 2"))
    assert _select(repo).tests == {f"{TEST_FILE}::test_value", f"{TEST_FILE}::test_other"}
    (repo / MODELS).write_text(SOURCE.replace('"""Models."""\n', '"""Models."""\nimport os  # noqa: F401\n'))
    assert _select(repo).tests == {f"{TEST_FILE}::test_value", f"{TEST_FILE}::test_other"}


def test_class_body_edit_selects_every_test_touching_the_file(repo: Path) -> None:
    # Class bodies run at import, outside every test, so the map never records the class itself.
    (repo / SETTINGS).write_text(SETTINGS_SOURCE.replace("limit = 1", "limit = 2"))
    assert _select(repo, SETTINGS).tests == {f"{TEST_FILE}::test_settings"}


def test_cosmetic_change_and_unmapped_file_are_reported(repo: Path) -> None:
    (repo / MODELS).write_text(SOURCE.replace('"""Models."""', '"""Domain models."""'))
    selection = _select(repo)
    assert selection.tests == set() and selection.cosmetic == {MODELS}
    (repo / UNMEASURED).write_text("X = 2\n")  # tracked, changed, but no test ever executed it
    (repo / MODELS).write_text(SOURCE.replace("    return LIMIT", "    return LIMIT + 0"))
    selection = _select(repo, MODELS, UNMEASURED)
    assert selection.unmapped == {UNMEASURED} and selection.tests == {f"{TEST_FILE}::test_value"}


def test_large_selections_widen_to_the_package_suite(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(impact, "WIDEN_RATIO", 0.25)
    (repo / MODELS).write_text(SOURCE.replace("LIMIT = 1", "LIMIT = 2"))
    selection = _select(repo)
    assert selection.widened and selection.tests == {"packages/a13n-core/tests"}


@pytest.mark.parametrize(
    "replacement",
    [
        TESTS + "\n\ndef test_new():\n    assert False\n",
        TESTS.replace("test_value", "test_renamed"),
        TESTS.replace(
            "def test_value():", "import pytest\n\n@pytest.mark.parametrize('value', [1, 2])\ndef test_value(value):"
        ),
    ],
    ids=["added", "renamed", "parametrized"],
)
def test_changed_test_file_runs_current_tests_instead_of_recorded_nodeids(repo: Path, replacement: str) -> None:
    (repo / TEST_FILE).write_text(replacement)
    result = verify.plan(verify.changed_files("HEAD"), graph=verify.PythonGraph(repo))
    assert result.python_tests == {TEST_FILE}


def test_deleted_source_uses_recorded_dependents_without_linting_missing_file(repo: Path) -> None:
    (repo / MODELS).unlink()
    files = verify.changed_files("HEAD")
    assert files == [MODELS]
    result = verify.plan(files, graph=verify.PythonGraph(repo))
    assert result.python_tests == {f"{TEST_FILE}::test_value", f"{TEST_FILE}::test_other"}
    assert not result.python_files


def test_deleted_unmapped_source_falls_back_to_own_package(repo: Path) -> None:
    (repo / UNMEASURED).unlink()
    result = verify.plan(verify.changed_files("HEAD"), graph=verify.PythonGraph(repo))
    assert result.python_tests == {f"packages/{PACKAGE}/tests"}
    assert not result.python_files


def test_renamed_source_keeps_old_path_for_selecting_dependents(repo: Path) -> None:
    renamed = MODELS.replace("models.py", "renamed.py")
    (repo / MODELS).rename(repo / renamed)
    _git(repo, "add", "-A")
    assert verify.changed_files("HEAD") == [MODELS, renamed]
    assert _select(repo, MODELS).tests == {f"{TEST_FILE}::test_value", f"{TEST_FILE}::test_other"}


def test_deleted_tests_are_not_selected_from_old_maps(repo: Path) -> None:
    (repo / MODELS).write_text(SOURCE.replace("return LIMIT", "return LIMIT + 1"))
    (repo / TEST_FILE).unlink()
    result = verify.plan(verify.changed_files("HEAD"), graph=verify.PythonGraph(repo))
    assert not result.python_tests


def test_verify_plan_prefers_the_map_and_falls_back_for_unmapped_files(repo: Path) -> None:
    (repo / MODELS).write_text(SOURCE.replace("    return LIMIT", "    return LIMIT + 0"))
    result = verify.plan([MODELS], graph=verify.PythonGraph(repo))
    assert result.python_tests == {f"{TEST_FILE}::test_value"}
    result = verify.plan([MODELS, TEST_FILE, "packages/a13n-core/tests/conftest.py"], graph=verify.PythonGraph(repo))
    assert result.python_tests == {"packages/a13n-core/tests"}
    steps = verify.steps_for(verify.plan([MODELS], graph=verify.PythonGraph(repo)))
    listing = steps[-1].command[2]
    assert listing.startswith("PYTHON_TEST_DIRS=@")
    assert Path(listing.split("@", 1)[1]).read_text().strip() == f"{TEST_FILE}::test_value"


def test_short_recorded_selections_run_without_xdist_workers(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    found = impact.find_map(PACKAGE)
    assert found is not None
    assert impact.recorded_seconds(found, [f"{TEST_FILE}::test_value", TEST_FILE]) == 3.5
    assert impact.recorded_seconds(found, ["packages/a13n-core/tests"]) == 3
    assert impact.recorded_seconds(found, [f"{TEST_FILE}::test_unrelated_a"]) is None
    (repo / MODELS).write_text(SOURCE.replace("LIMIT = 1", "LIMIT = 2"))
    result = verify.plan([MODELS], graph=verify.PythonGraph(repo))
    assert result.python_seconds == 2
    assert verify.steps_for(result)[-1].command[-1] == "PYTHON_TEST_WORKERS=0"
    monkeypatch.setattr(verify, "SERIAL_SECONDS", 1)
    assert "PYTHON_TEST_WORKERS=0" not in verify.steps_for(result)[-1].command


def test_recording_under_xdist_writes_a_map_without_group_suffixes(tmp_path: Path) -> None:
    root = Path(verify.REPOSITORY_ROOT)
    target = tmp_path / "map.json.gz"
    env = {
        **os.environ,
        impact.MAP_VARIABLE: str(target),
        impact.COVERAGE_VARIABLE: str(tmp_path / "coverage"),
        "COVERAGE_CORE": "ctrace",
        "PYTHONPATH": str(root),
        "PYTEST_ADDOPTS": "",
    }
    command = [sys.executable, "-m", "pytest", "scripts/tests/test_verify.py", "-q", "-n", "2", "-p", "scripts.impact"]
    subprocess.run(command, cwd=root, env=env, check=True, capture_output=True)
    with gzip.open(target, "rt") as handle:
        document = json.load(handle)
    nodeids = set(document["tests"])
    assert any(nodeid.endswith("::test_parent_directory_absorbs_child_selections") for nodeid in nodeids)
    assert not any("@" in nodeid for nodeid in nodeids)
    assert set(document["durations"]) >= nodeids
    assert "plan" in set(document["funcmaps"]["scripts/verify.py"].values())
    assert document["ref"] == _git(root, "rev-parse", "HEAD")
