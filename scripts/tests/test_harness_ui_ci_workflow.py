from __future__ import annotations

import ast
import shlex
import subprocess
from pathlib import Path

import pytest
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
    assert jobs["tests"]["if"] == (
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
            ".github/workflows/ci-a13n-harness-ui*.yml",
            "scripts/tests/test_harness_ui_ci_workflow.py",
            "docs/a13n-harness-ui/**",
            "mkdocs.yml",
        } <= set(triggers[event]["paths"])


@pytest.mark.parametrize(
    "workflow_name,job",
    [
        ("ci-a13n-harness-ui.yml", "tests"),
        ("ci-a13n-harness-ui.yml", "distribution"),
        ("ci-a13n-harness-ui.yml", "windows"),
        ("ci-a13n-harness-ui-webui.yml", "webui"),
    ],
)
def test_editable_ui_jobs_prepare_skills_after_dependency_sync(workflow_name: str, job: str) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows" / workflow_name).read_text())
    steps = workflow["jobs"][job]["steps"]
    sync = next(index for index, step in enumerate(steps) if step.get("run", "").startswith("uv sync "))
    prepare = steps[sync + 1]
    assert "if" not in prepare
    assert shlex.split(prepare["run"]) == [
        "uv",
        "run",
        "--locked",
        "--no-sync",
        "python",
        "packages/a13n-harness-ui/build_skills.py",
    ]


@pytest.mark.parametrize(
    "target,consumer",
    [
        ("a13n-harness-ui", "uv run --locked a13n-harness-ui --no-update-check"),
        ("a13n-harness-ui-assets", "scripts/prepare-a13n-harness-ui-assets.py"),
        ("test", "scripts.run_python_tests"),
    ],
)
def test_source_ui_consumers_prepare_skills(target: str, consumer: str) -> None:
    result = subprocess.run(["make", "--dry-run", target], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.index("uv sync ") < result.stdout.index("packages/a13n-harness-ui/build_skills.py")
    assert result.stdout.index("packages/a13n-harness-ui/build_skills.py") < result.stdout.index(consumer)


def test_linux_keeps_full_tests_and_distribution_checks() -> None:
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["tests"]["steps"]
    by_name = {step["name"]: step for step in steps}
    tests = by_name["Test UI"]
    arguments = shlex.split(tests["run"])
    assert "scripts.run_python_tests" in arguments
    assert arguments[arguments.index("--workers") + 1] == "2"
    assert "packages/a13n-harness-ui/tests" in arguments
    assert not any(arg.startswith("scripts/tests/") for arg in arguments)
    assert "--durations=20" in tests["env"]["PYTEST_ADDOPTS"]
    for test in ("test_webui.py", "test_webui_startup.py"):
        assert f"--ignore=packages/a13n-harness-ui/tests/{test}" in tests["env"]["PYTEST_ADDOPTS"]
    assert "Test native command lifecycle" in by_name
    assert not any("pnpm" in step.get("run", "") for step in steps)
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    for name in ("tests", "frontend", "distribution"):
        assert "needs" not in jobs[name]
        assert jobs[name]["if"] == jobs["tests"]["if"]
    frontend = "\n".join(step.get("run", "") for step in jobs["frontend"]["steps"])
    assert "--filter '!a13n-harness-ui-webui' -r run check" in frontend
    assert "--filter '!a13n-harness-ui-webui' -r run build" in frontend
    distribution = {step["name"]: step for step in jobs["distribution"]["steps"]}
    assert "--filter a13n-harness-ui-webui run build" in distribution["Build WebUI assets"]["run"]
    assert "scripts/tests/test_harness_ui_ci_workflow.py" in distribution["Test distribution tooling"]["run"]
    for name in (
        "Prepare bundled WebUI assets",
        "Build UI distribution",
        "Verify bundled assets and sdist wheel rebuild",
    ):
        assert "if" not in distribution[name]
    assert jobs["linux"]["needs"] == ["tests", "frontend", "distribution"]
    assert jobs["linux"]["name"] == "UI (Linux)"
    assert jobs["linux"]["if"].startswith("always() &&")


@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped"])
@pytest.mark.parametrize("component", ["TESTS", "FRONTEND", "DISTRIBUTION"])
def test_linux_gate_rejects_any_unsuccessful_job(component: str, result: str) -> None:
    gate = yaml.safe_load(WORKFLOW.read_text())["jobs"]["linux"]["steps"][0]
    env = {f"{name}_RESULT": "success" for name in ("TESTS", "FRONTEND", "DISTRIBUTION")}
    env[f"{component}_RESULT"] = result
    completed = subprocess.run(["bash", "-e", "-c", gate["run"]], env=env, check=False)
    assert (completed.returncode == 0) == (result == "success")


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
    full = by_name["Test full UI suite on Windows"]
    assert full["if"] == "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
    assert shlex.split(full["run"])[-1] == "packages/a13n-harness-ui/tests"
    lifecycle = by_name["Test native command lifecycle"]
    assert "if" not in lifecycle
    for name in ("test_direct_local.py", "test_direct_local_processes.py"):
        assert f"packages/a13n-environment/tests/{name}" in shlex.split(lifecycle["run"])


def test_webui_ci_only_runs_unit_contract_and_http_tests() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci-a13n-harness-ui-webui.yml").read_text())
    assert "schedule" not in workflow[True]
    assert len(workflow["jobs"]) == 1
    steps = workflow["jobs"]["webui"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    assert "test_webui.py" in commands and "test_webui_startup.py" in commands
    assert "a13n-harness-ui-webui run check" in commands
    assert "docker" not in str(steps).lower()
    assert "uv build" not in commands


def test_bundled_documentation_is_a_ui_image_input() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/images.yml").read_text())
    inputs = {"docs/a13n-harness-ui/**", "mkdocs.yml"}
    assert inputs <= set(workflow[True]["push"]["paths"])
    step = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
    filters = yaml.safe_load(step["with"]["filters"])
    assert inputs <= set(filters["harness_ui"])
    assert not inputs.intersection(filters["service"])
