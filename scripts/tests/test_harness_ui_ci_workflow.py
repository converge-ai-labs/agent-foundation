from __future__ import annotations

import ast
import shlex
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github/workflows/ci-a13n-harness-ui.yml"


def test_ui_ci_keeps_main_linux_and_separate_windows_backstop() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    # PyYAML's YAML 1.1 loader reads the Actions `on` key as True.
    triggers = workflow[True]
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["schedule"] == [{"cron": "23 4 * * 1"}]
    assert "workflow_dispatch" in triggers
    assert "github.event_name" in workflow["concurrency"]["group"]
    jobs = workflow["jobs"]
    assert jobs["linux"]["if"] == (
        "github.event_name != 'schedule' && "
        "(github.event_name != 'pull_request' || github.event.pull_request.draft == false)"
    )
    assert jobs["windows"]["if"] == (
        "github.event_name != 'push' && "
        "(github.event_name != 'pull_request' || github.event.pull_request.draft == false)"
    )
    for event in ("push", "pull_request"):
        assert {
            "conftest.py",
            "scripts/run_python_tests.py",
            str(WORKFLOW.relative_to(ROOT)),
            "scripts/tests/test_harness_ui_ci_workflow.py",
        } <= set(triggers[event]["paths"])


def test_linux_keeps_full_tests_and_distribution_checks() -> None:
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["linux"]["steps"]
    by_name = {step["name"]: step for step in steps}
    tests = by_name["Test Harness UI"]
    arguments = shlex.split(tests["run"])
    assert "scripts.run_python_tests" in arguments
    assert arguments[arguments.index("--workers") + 1] == "2"
    assert "packages/a13n-harness-ui/tests" in arguments
    assert "scripts/tests/test_harness_ui_ci_workflow.py" in arguments
    assert tests["env"]["PYTEST_ADDOPTS"] == "--durations=20"
    for name in (
        "Install and check frontend workspace",
        "Test native command lifecycle",
        "Build Harness UI distribution",
        "Verify bundled assets and sdist wheel rebuild",
    ):
        assert "if" not in by_name[name]


def test_windows_native_selection_exists_and_full_suite_is_retained() -> None:
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["windows"]["steps"]
    by_name = {step["name"]: step for step in steps}
    native = by_name["Test Windows native UI integration"]
    assert native["if"] == "github.event_name == 'pull_request'"
    selections = [arg for arg in shlex.split(native["run"]) if arg.startswith("packages/")]
    assert selections
    for selection in selections:
        path, _, node = selection.partition("::")
        source = ROOT / path
        assert source.is_file(), selection
        if node:
            names = {
                item.name
                for item in ast.parse(source.read_text()).body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assert node in names, selection
    assert "packages/a13n-harness-ui/tests/test_entrypoint.py" in selections
    assert "packages/a13n-harness-ui/tests/test_thread_files.py" in selections
    assert "packages/a13n-harness-ui/tests/test_configuration_mutation.py" in selections
    full = by_name["Test full Harness UI suite on Windows"]
    assert full["if"] == "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
    assert shlex.split(full["run"])[-1] == "packages/a13n-harness-ui/tests"
    lifecycle = by_name["Test native command lifecycle"]
    assert "if" not in lifecycle
    for name in ("test_direct_local.py", "test_direct_local_processes.py"):
        assert f"packages/a13n-environment/tests/{name}" in shlex.split(lifecycle["run"])
