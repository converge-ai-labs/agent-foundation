from __future__ import annotations

from pathlib import Path

import pytest

from scripts import verify


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A miniature workspace: two distributions, cross-package imports, tests and scripts."""
    files = {
        "packages/a13n-core/a13n_core/__init__.py": "",
        "packages/a13n-core/a13n_core/models.py": "VALUE = 1\n",
        "packages/a13n-core/a13n_core/unused.py": "VALUE = 2\n",
        "packages/a13n-core/tests/conftest.py": "",
        "packages/a13n-core/tests/support.py": "from a13n_core.models import VALUE\n",
        "packages/a13n-core/tests/test_models.py": "from tests.support import VALUE\n",
        "packages/a13n-core/tests/test_other.py": "def test_other(): pass\n",
        "packages/a13n-app/a13n_app/__init__.py": "",
        "packages/a13n-app/a13n_app/service.py": "from a13n_core import models\n",
        "packages/a13n-app/tests/conftest.py": "",
        "packages/a13n-app/tests/test_service.py": "from a13n_app.service import models\n",
        "packages/a13n-app/tests/nested/test_deep.py": "from .. import conftest\n",
        "scripts/tool.py": "",
        "scripts/tests/test_tool.py": "from scripts import tool\n",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    monkeypatch.setattr(verify, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(verify, "PACKAGES", tmp_path / "packages")
    monkeypatch.setattr(verify, "SCRIPTS", tmp_path / "scripts")
    monkeypatch.setattr(verify, "FRONTEND", tmp_path / "frontend")
    return tmp_path


def _tests(workspace: Path, *files: str) -> set[str]:
    return verify.plan(files, graph=verify.PythonGraph(workspace)).python_tests


def test_changed_module_selects_its_own_tests_and_reports_consumers(workspace: Path) -> None:
    result = verify.plan(["packages/a13n-core/a13n_core/models.py"], graph=verify.PythonGraph(workspace))
    assert result.python_tests == {"packages/a13n-core/tests/test_models.py"}
    assert result.consumer_tests == {"packages/a13n-app/tests/test_service.py"}
    assert any("--consumers" in note for note in result.notes)
    widened = verify.plan(
        ["packages/a13n-core/a13n_core/models.py"], graph=verify.PythonGraph(workspace), consumers=True
    )
    assert widened.python_tests == {
        "packages/a13n-core/tests/test_models.py",
        "packages/a13n-app/tests/test_service.py",
    }


def test_test_helper_and_conftest_select_their_scope(workspace: Path) -> None:
    assert _tests(workspace, "packages/a13n-core/tests/support.py") == {"packages/a13n-core/tests/test_models.py"}
    assert _tests(workspace, "packages/a13n-app/tests/conftest.py") == {"packages/a13n-app/tests"}


def test_unimported_module_falls_back_to_its_package_suite(workspace: Path) -> None:
    result = verify.plan(["packages/a13n-core/a13n_core/unused.py"], graph=verify.PythonGraph(workspace))
    assert result.python_tests == {"packages/a13n-core/tests"}
    assert any("no test imports" in note for note in result.notes)


def test_global_inputs_select_everything_and_scripts_select_their_tests(workspace: Path) -> None:
    assert _tests(workspace, "uv.lock", "packages/a13n-core/a13n_core/models.py") == {"ALL"}
    assert _tests(workspace, "scripts/tool.py") == {"scripts/tests"}


def test_parent_directory_absorbs_child_selections(workspace: Path) -> None:
    assert _tests(workspace, "packages/a13n-app/tests/conftest.py", "packages/a13n-app/tests/test_service.py") == {
        "packages/a13n-app/tests"
    }


def test_frontend_sources_select_related_files_and_project_inputs_select_full_runs(workspace: Path) -> None:
    for relative in ("frontend/apps/a13n-console/src/app.tsx", "frontend/packages/a13n-ui/vitest.config.ts"):
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    result = verify.plan(["frontend/apps/a13n-console/src/app.tsx", "frontend/packages/a13n-ui/vitest.config.ts"])
    assert result.frontend_full == {"packages/a13n-ui"}
    assert set(result.frontend_related) == {"apps/a13n-console", "apps/a13n-harness-ui"}
    names = [step.name for step in verify.steps_for(result)]
    assert names == [
        "prettier",
        "typecheck apps/a13n-console",
        "typecheck packages/a13n-ui",
        "vitest packages/a13n-ui",
        "vitest related apps/a13n-console",
        "vitest related apps/a13n-harness-ui",
    ]


def test_python_steps_lint_changed_files_and_run_the_selection(workspace: Path) -> None:
    result = verify.plan(["packages/a13n-core/a13n_core/models.py"], graph=verify.PythonGraph(workspace))
    steps = verify.steps_for(result)
    assert [step.name for step in steps] == ["ruff check", "ruff format", "pyright", "python tests"]
    assert steps[-1].command[:2] == ["make", "test"]
    assert "-q --tb=short" in steps[-1].env["PYTEST_ADDOPTS"]


def test_deleted_source_without_maps_runs_own_suite(workspace: Path) -> None:
    path = "packages/a13n-core/a13n_core/models.py"
    (workspace / path).unlink()
    result = verify.plan([path], graph=verify.PythonGraph(workspace))
    assert result.python_tests == {"packages/a13n-core/tests"}
    assert not result.python_files


def test_deleted_frontend_source_runs_project_without_formatting_missing_file(workspace: Path) -> None:
    result = verify.plan(["frontend/apps/a13n-console/src/deleted.tsx"])
    assert result.frontend_full == {"apps/a13n-console"}
    assert not result.frontend_files


def test_deleted_shared_frontend_source_runs_consumers(workspace: Path) -> None:
    result = verify.plan(["frontend/packages/a13n-ui/src/deleted.tsx"])
    assert result.frontend_full == set(verify.FRONTEND_PROJECTS)


def test_deleted_documentation_does_not_select_tests_or_format_missing_files(workspace: Path) -> None:
    result = verify.plan(["frontend/apps/a13n-console/README.md", "CONTRIBUTING.md"])
    assert not verify.steps_for(result)
