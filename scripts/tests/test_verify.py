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
    assert _tests(workspace, "scripts/tool.py") == {"scripts/tests/test_tool.py"}
    # Make recipes do not change how tests behave; `make test` itself runs whenever tests are selected.
    assert _tests(workspace, "Makefile") == set()


@pytest.mark.parametrize(
    "path",
    [
        "Makefile",
        "examples/mcp-apps/src/mcp_apps_example/server.py",
        "examples/mcp-apps/tests/test_server.py",
        "examples/mcp-apps/app.js",
        "examples/mcp-apps/app.html",
        "examples/mcp-apps/build.mjs",
        "examples/mcp-apps/pyproject.toml",
        "examples/mcp-apps/uv.lock",
        "examples/mcp-apps/package.json",
        "examples/mcp-apps/package-lock.json",
    ],
)
def test_example_inputs_use_the_independent_build_and_test_gate(workspace: Path, path: str) -> None:
    result = verify.plan([path], graph=verify.PythonGraph(workspace))
    assert result.examples
    assert not result.python_tests
    assert [step.command for step in verify.steps_for(result)] == [["make", "examples-check-all"]]
    assert ["make", "examples-check-all"] in [step.command for step in verify.full_steps()]


def test_example_readme_does_not_select_execution(workspace: Path) -> None:
    assert not verify.plan(["examples/mcp-apps/README.md"]).examples


def test_migration_revisions_without_importers_run_their_package_suite(workspace: Path) -> None:
    revision = workspace / "packages/a13n-core/a13n_core/migrations/versions/20260101_revision.py"
    revision.parent.mkdir(parents=True)
    revision.write_text("def upgrade():\n    pass\n")
    assert _tests(workspace, revision.relative_to(workspace).as_posix()) == {"packages/a13n-core/tests"}


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
    # An application source is related only to its own tests.
    assert set(result.frontend_related) == {"apps/a13n-console"}
    names = [step.name for step in verify.steps_for(result)]
    assert names == [
        "prettier",
        "typecheck apps/a13n-console",
        "typecheck packages/a13n-ui",
        "vitest packages/a13n-ui",
        "vitest related apps/a13n-console",
    ]
    shared = workspace / "frontend/packages/a13n-ui/src/button.tsx"
    shared.parent.mkdir(parents=True)
    shared.write_text("")
    assert set(verify.plan(["frontend/packages/a13n-ui/src/button.tsx"]).frontend_related) == set(
        verify.FRONTEND_PROJECTS
    )


def test_python_steps_lint_changed_files_and_run_the_selection(workspace: Path) -> None:
    result = verify.plan(["packages/a13n-core/a13n_core/models.py"], graph=verify.PythonGraph(workspace))
    steps = verify.steps_for(result)
    assert [step.name for step in steps] == ["ruff check", "ruff format", "pyright", "python tests"]
    assert steps[-1].command[:2] == ["make", "test"]
    assert "-q --tb=short" in steps[-1].env["PYTEST_ADDOPTS"]


def test_build_hooks_are_linted_but_not_type_checked(workspace: Path) -> None:
    hook = workspace / "packages/a13n-core/hatch_build.py"
    hook.write_text("")
    result = verify.plan(["packages/a13n-core/hatch_build.py"], graph=verify.PythonGraph(workspace))
    names = [step.name for step in verify.steps_for(result)]
    assert names[:2] == ["ruff check", "ruff format"] and "pyright" not in names


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


def test_tooling_tests_and_readmes_do_not_run_unrelated_tests(workspace: Path) -> None:
    assert _tests(workspace, "scripts/tests/test_tool.py") == {"scripts/tests/test_tool.py"}
    assert _tests(workspace, "scripts/docs/README.md") == set()


def test_tooling_helpers_and_bare_script_imports_follow_transitive_dependencies(workspace: Path) -> None:
    (workspace / "scripts/helper.py").write_text("VALUE = 1\n")
    (workspace / "scripts/tool.py").write_text("from helper import VALUE\n")
    assert _tests(workspace, "scripts/helper.py") == {"scripts/tests/test_tool.py"}


def test_subprocess_edges_propagate_imported_library_changes(workspace: Path, monkeypatch) -> None:
    from scripts import verify_dependencies

    (workspace / "scripts/helper.py").write_text("VALUE = 1\n")
    (workspace / "scripts/tool.py").write_text("from helper import VALUE\n")
    (workspace / "scripts/tests/test_tool.py").write_text("# Executes tool.py through subprocess\n")
    monkeypatch.setattr(verify_dependencies, "TEST_INPUTS", {"test_tool.py": ("scripts/tool.py",)})
    assert _tests(workspace, "scripts/helper.py") == {"scripts/tests/test_tool.py"}


def test_unmapped_tools_still_widen_when_another_change_has_known_tests(workspace: Path) -> None:
    (workspace / "scripts/new_tool.py").write_text("VALUE = 1\n")
    result = verify.plan(["scripts/tool.py", "scripts/new_tool.py"], graph=verify.PythonGraph(workspace))
    assert result.python_tests == {"scripts/tests"}
    assert any("no test imports scripts/new_tool.py" in note for note in result.notes)
    assert _tests(workspace, "scripts/unknown.sh") == {"scripts/tests"}
    assert _tests(workspace, ".github/scripts/unknown.cjs") == {"scripts/tests"}


def test_deleted_script_uses_existing_suite_without_linting_missing_path(workspace: Path) -> None:
    (workspace / "scripts/tool.py").unlink()
    result = verify.plan(["scripts/tool.py"], graph=verify.PythonGraph(workspace))
    assert result.python_tests == {"scripts/tests"}
    assert not result.python_files


def test_tooling_conftest_selects_its_subtree(workspace: Path) -> None:
    nested = workspace / "scripts/tests/nested"
    nested.mkdir()
    (nested / "conftest.py").touch()
    assert _tests(workspace, "scripts/tests/nested/conftest.py") == {"scripts/tests/nested"}
    assert _tests(workspace, "scripts/tests/conftest.py") == {"scripts/tests"}


def test_real_report_inputs_and_workflow_have_focused_coverage() -> None:
    test = "scripts/tests/test_pr_change_breakdown.py"
    assert verify.plan([".github/scripts/pr-change-rules.cjs"]).python_tests == {test}
    assert verify.plan([test]).python_tests == {test}
    result = verify.plan([".github/workflows/pr-change-breakdown.yml"])
    assert result.python_tests == {test, "scripts/tests/test_pr_labels.py"}
    assert result.workflows == {".github/workflows/pr-change-breakdown.yml"}
    step = verify.steps_for(result)[0]
    assert step.name == "actionlint"
    assert step.command[:3] == ["docker", "run", "--rm"]
    assert step.command[-1] == ".github/workflows/pr-change-breakdown.yml"
    assert "$PWD" not in " ".join(step.command)
    assert any("declared file/command dependencies" in note for note in result.notes)


def test_real_release_library_keeps_subprocess_consumers() -> None:
    result = verify.plan(["scripts/release_version.py"])
    assert {
        "scripts/tests/test_check_release_version.py",
        "scripts/tests/test_prepare_release_version.py",
        "scripts/tests/test_release_notes.py",
    } <= result.python_tests
    assert "scripts/tests" not in result.python_tests


def test_deleted_workflow_and_actionlint_config_validate_remaining_workflows(workspace: Path) -> None:
    deleted = verify.plan([".github/workflows/deleted.yml"])
    assert deleted.lint_all_workflows
    assert not deleted.workflows
    assert verify.plan([".github/actionlint.yaml"]).lint_all_workflows


def test_change_discovery_preserves_renames_deletions_and_unusual_names(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    monkeypatch.setattr(verify, "REPOSITORY_ROOT", tmp_path)

    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    for name in ("deleted.py", "old.py"):
        (tmp_path / name).write_text("value = 1\n")
    git("add", ".")
    git("commit", "-m", "initial")
    (tmp_path / "deleted.py").unlink()
    (tmp_path / "old.py").rename(tmp_path / "new.py")
    odd = "space and\nnewline.py"
    (tmp_path / odd).touch()
    assert set(verify.changed_files("HEAD")) == {"deleted.py", "old.py", "new.py", odd}


def test_verify_covers_the_changes_since_the_last_passing_run(tmp_path: Path, monkeypatch, capsys) -> None:
    import subprocess

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (tmp_path / "tracked.py").write_text("value = 1\n")
    git("add", ".")
    git("commit", "-m", "initial")
    monkeypatch.setattr(verify, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(verify, "DEFAULT_BASE", "HEAD")
    planned: list[list[str]] = []
    outcome = [0]

    def plan(files, *, consumers=False):
        planned.append(sorted(files))
        return verify.Plan(markdown_files={"changed.md"})

    monkeypatch.setattr(verify, "plan", plan)
    monkeypatch.setattr(verify, "run", lambda steps, *, dry_run: outcome[0])

    (tmp_path / "first.py").write_text("")
    (tmp_path / "tracked.py").write_text("value = 2\n")
    assert verify.main([]) == 0
    assert planned[-1] == ["first.py", "tracked.py"]
    (tmp_path / "second.py").write_text("")
    outcome[0] = 1
    assert verify.main([]) == 1
    assert planned[-1] == ["second.py"]
    # A failed run records nothing, so its changes stay selected.
    (tmp_path / "tracked.py").write_text("value = 1\n")
    outcome[0] = 0
    assert verify.main([]) == 0
    assert planned[-1] == ["second.py", "tracked.py"]
    assert verify.main([]) == 0
    assert "no changes since the last passing verify" in capsys.readouterr().out
    # Consumers were not part of the passing run, and --base asks for the whole branch.
    assert verify.main(["--consumers"]) == 0
    assert planned[-1] == ["first.py", "second.py"]
    assert verify.main(["--base", "HEAD"]) == 0
    assert planned[-1] == ["first.py", "second.py"]


def test_snapshot_sees_a_same_size_edit_made_in_the_indexed_second(tmp_path: Path, monkeypatch) -> None:
    import os
    import subprocess

    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

    monkeypatch.setattr(verify, "REPOSITORY_ROOT", tmp_path)
    source = tmp_path / "tracked.py"
    source.write_text("value = 1\n")
    os.utime(source, (1_000_000_000, 1_000_000_000))
    git("init")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "initial")
    # Same size and timestamp as the index entry: only Git's same-second check can notice the edit.
    os.utime(tmp_path / ".git/index", (1_000_000_000, 1_000_000_000))
    source.write_text("value = 2\n")
    os.utime(source, (1_000_000_000, 1_000_000_000))
    assert verify.changes_between("HEAD", verify.snapshot()) == ["tracked.py"]
