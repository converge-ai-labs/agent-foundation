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
MODELS = "packages/a13n-core/a13n_core/models.py"
TEST_FILE = "packages/a13n-core/tests/test_models.py"
PACKAGE = "a13n-core"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A committed miniature repository with a recorded map: two mapped tests, two unrelated ones."""
    for relative, content in {MODELS: SOURCE, TEST_FILE: TESTS}.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True)
        path.write_text(content)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@example.com", "add", ".")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "base")
    monkeypatch.setattr(impact, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(verify, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(verify, "PACKAGES", tmp_path / "packages")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    document = {
        "version": 5,
        "ref": _git(tmp_path, "rev-parse", "HEAD"),
        "tests": {
            f"{TEST_FILE}::test_value": {MODELS: ["value"], TEST_FILE: ["test_value"]},
            f"{TEST_FILE}::test_other": {MODELS: ["other"], TEST_FILE: ["test_other"]},
            f"{TEST_FILE}::test_unrelated_a": {},
            f"{TEST_FILE}::test_unrelated_b": {},
        },
        "reads": {},
        "funcmaps": {
            relative: {str(n): q for n, q in astmap.line_to_qualname_from_file(str(tmp_path / relative)).items()}
            for relative in (MODELS, TEST_FILE)
        },
        "dynamic": {},
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
    assert found is not None and found.distance == 0 and len(found.tests) == 4
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


def test_cosmetic_change_and_unmapped_file_are_reported(repo: Path) -> None:
    (repo / MODELS).write_text(SOURCE.replace('"""Models."""', '"""Domain models."""'))
    selection = _select(repo)
    assert selection.tests == set() and selection.cosmetic == {MODELS}
    new = repo / "packages/a13n-core/a13n_core/extra.py"
    new.write_text("X = 1\n")
    assert _select(repo, "packages/a13n-core/a13n_core/extra.py").unmapped == {"packages/a13n-core/a13n_core/extra.py"}


def test_large_selections_widen_to_the_package_suite(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(impact, "WIDEN_RATIO", 0.25)
    (repo / MODELS).write_text(SOURCE.replace("LIMIT = 1", "LIMIT = 2"))
    selection = _select(repo)
    assert selection.widened and selection.tests == {"packages/a13n-core/tests"}


def test_verify_plan_prefers_the_map_and_falls_back_for_unmapped_files(repo: Path) -> None:
    (repo / MODELS).write_text(SOURCE.replace("    return LIMIT", "    return LIMIT + 0"))
    result = verify.plan([MODELS], graph=verify.PythonGraph(repo))
    assert result.python_tests == {f"{TEST_FILE}::test_value"}
    result = verify.plan([MODELS, TEST_FILE, "packages/a13n-core/tests/conftest.py"], graph=verify.PythonGraph(repo))
    assert result.python_tests == {"packages/a13n-core/tests"}
    steps = verify.steps_for(verify.plan([MODELS], graph=verify.PythonGraph(repo)))
    listing = steps[-1].command[-1]
    assert listing.startswith("PYTHON_TEST_DIRS=@")
    assert Path(listing.split("@", 1)[1]).read_text().strip() == f"{TEST_FILE}::test_value"


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
    assert "plan" in set(document["funcmaps"]["scripts/verify.py"].values())
    assert document["ref"] == _git(root, "rev-parse", "HEAD")
