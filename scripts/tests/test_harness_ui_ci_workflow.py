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
    assert triggers["schedule"]
    assert all("cron" in schedule for schedule in triggers["schedule"])
    assert "workflow_dispatch" in triggers
    assert "github.event_name" in workflow["concurrency"]["group"]
    jobs = workflow["jobs"]
    assert workflow["permissions"]["pull-requests"] == "read"
    assert jobs["changes"]["if"] == ("github.event_name != 'pull_request' || github.event.pull_request.draft == false")
    checkout = jobs["changes"]["steps"][0]
    assert checkout["with"]["fetch-depth"] == 0
    classifier = jobs["changes"]["steps"][1]
    assert classifier["if"] == "github.event_name == 'pull_request' || github.event_name == 'push'"
    assert jobs["windows"]["needs"] == "changes"
    assert jobs["windows"]["if"].strip() == (
        "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch' || "
        "(github.event_name == 'pull_request' && needs.changes.outputs.tests == 'true')"
    )
    for event in ("push", "pull_request"):
        assert {
            "conftest.py",
            "scripts/run_python_tests.py",
            ".github/workflows/ci-a13n-harness-ui*.yml",
            "scripts/tests/test_harness_ui_ci_workflow.py",
            "docs/a13n-harness-ui/**",
        } <= set(triggers[event]["paths"])


@pytest.mark.parametrize(
    "workflow_name,job",
    [
        ("ci-a13n-harness-ui.yml", "tests"),
        ("ci-a13n-harness-ui.yml", "distribution"),
        ("ci-a13n-harness-ui.yml", "windows"),
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
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    steps = jobs["tests"]["steps"]
    by_name = {step["name"]: step for step in steps}
    tests = by_name["Test UI"]
    arguments = shlex.split(tests["run"])
    assert "scripts.run_python_tests" in arguments
    assert jobs["tests"]["runs-on"] == "ubuntu-24.04"
    assert jobs["distribution"]["runs-on"] == "ubuntu-24.04"
    assert int(arguments[arguments.index("--workers") + 1]) == 3
    assert "packages/a13n-harness-ui/tests" in arguments
    assert not any(arg.startswith("scripts/tests/") for arg in arguments)
    options = shlex.split(tests["env"]["PYTEST_ADDOPTS"])
    assert {"--timeout-method=thread", "--max-worker-restart=0"} <= set(options)
    assert any(option.startswith("--durations=") for option in options)
    watchdog = float(next(option.split("=", 1)[1] for option in options if option.startswith("--timeout=")))
    dump = float(next(option.split("=", 1)[1] for option in options if option.startswith("faulthandler_timeout=")))
    assert 0 < dump < watchdog < tests["timeout-minutes"] * 60 < jobs["tests"]["timeout-minutes"] * 60
    assert any(option.startswith("--timeout=") for option in shlex.split(jobs["windows"]["env"]["PYTEST_ADDOPTS"]))
    assert "Test native command lifecycle" in by_name
    assert not any("pnpm" in step.get("run", "") for step in steps)
    for name in ("tests", "frontend", "distribution"):
        assert jobs[name]["needs"] == "changes"
        assert jobs[name]["if"] == (
            f"github.event_name == 'workflow_dispatch' || needs.changes.outputs.{name} == 'true'"
        )
        assert jobs["changes"]["outputs"][name] == "${{ steps.filter.outputs." + name + " }}"
    frontend = "\n".join(step.get("run", "") for step in jobs["frontend"]["steps"])
    assert "--filter '!a13n-harness-ui-webui' --filter '!a13n-docs' --filter '!a13n-site' -r run check" in frontend
    assert "--filter '!a13n-harness-ui-webui' --filter '!a13n-docs' --filter '!a13n-site' -r run test" in frontend
    assert "--filter '!a13n-harness-ui-webui' --filter '!a13n-docs' --filter '!a13n-site' -r run build" in frontend
    distribution = {step["name"]: step for step in jobs["distribution"]["steps"]}
    webui = "\n".join(step.get("run", "") for step in jobs["distribution"]["steps"])
    for script, name in (("check", "Check WebUI"), ("test", "Test WebUI"), ("build", "Build WebUI assets")):
        command = f"--filter a13n-harness-ui-webui run {script}"
        assert webui.count(command) == 1
        assert command in distribution[name]["run"]
        assert "if" not in distribution[name]
    assert "/usr/bin/time -p" in distribution["Test WebUI"]["run"]
    assert "--maxWorkers=2" in distribution["Test WebUI"]["run"]
    assert webui.index("run check") < webui.index("run test") < webui.index("run build")
    assert not any("playwright" in step.get("run", "").lower() for step in jobs["distribution"]["steps"])
    assert "scripts/tests/test_harness_ui_ci_workflow.py" in distribution["Test distribution tooling"]["run"]
    for name in (
        "Prepare bundled WebUI assets",
        "Build UI distribution",
        "Verify bundled assets and sdist wheel rebuild",
    ):
        assert "if" not in distribution[name]
    assert "prettier --check apps/a13n-harness-ui" in webui
    assert jobs["linux"]["needs"] == ["changes", "tests", "frontend", "distribution"]
    assert jobs["linux"]["name"] == "UI (Linux)"
    assert jobs["linux"]["if"].startswith("always() &&")


@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped", ""])
@pytest.mark.parametrize("selected", [True, False])
@pytest.mark.parametrize("component", ["TESTS", "FRONTEND", "DISTRIBUTION"])
def test_linux_gate_requires_exact_selected_results(component: str, selected: bool, result: str) -> None:
    gate = yaml.safe_load(WORKFLOW.read_text())["jobs"]["linux"]["steps"][0]
    env = {"CHANGES_RESULT": "success"}
    for name in ("TESTS", "FRONTEND", "DISTRIBUTION"):
        env[f"{name}_RESULT"] = "success"
        env[f"{name}_SELECTED"] = "true"
        assert gate["env"][f"{name}_SELECTED"] == (
            "${{ github.event_name == 'workflow_dispatch' || needs.changes.outputs." + name.lower() + " == 'true' }}"
        )
        assert gate["env"][f"{name}_RESULT"] == "${{ needs." + name.lower() + ".result }}"
    env[f"{component}_RESULT"] = result
    env[f"{component}_SELECTED"] = str(selected).lower()
    completed = subprocess.run(["bash", "-e", "-c", gate["run"]], env=env, check=False)
    assert (completed.returncode == 0) == (result == ("success" if selected else "skipped"))


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", ""])
def test_linux_gate_rejects_failed_classification(result: str) -> None:
    gate = yaml.safe_load(WORKFLOW.read_text())["jobs"]["linux"]["steps"][0]
    assert gate["env"]["CHANGES_RESULT"] == "${{ needs.changes.result }}"
    completed = subprocess.run(["bash", "-e", "-c", gate["run"]], env={"CHANGES_RESULT": result}, check=False)
    assert completed.returncode != 0


@pytest.mark.parametrize(
    "paths,expected",
    [
        (["frontend/apps/a13n-console/src/features/traces/detail.tsx"], {"frontend"}),
        (["frontend/apps/a13n-console/src/service-client/client.ts"], {"frontend"}),
        (["proto/a13n-service/openapi.json"], {"frontend"}),
        (["sdk/typescript/src/client.ts"], set()),
        (["frontend/apps/a13n-harness-ui/src/shell/workbench.tsx"], {"distribution"}),
        (["frontend/apps/a13n-harness-ui/src/openapi.json"], {"distribution"}),
        (["frontend/packages/a13n-ui/src/components/button.tsx"], {"frontend", "distribution"}),
        (["frontend/package.json"], {"frontend", "distribution"}),
        (["frontend/pnpm-lock.yaml"], {"frontend", "distribution"}),
        (["frontend/.prettierignore"], {"frontend", "distribution"}),
        (["packages/a13n-harness-ui/tests/test_cli_interactions.py"], {"tests"}),
        (["packages/a13n-harness-ui/tests/conftest.py"], {"tests"}),
        (["packages/a13n-environment/tests/test_direct_local.py"], {"tests"}),
        (["packages/a13n-harness-ui/a13n_harness_ui/app.py"], {"tests", "distribution"}),
        (["packages/a13n-harness-ui/build_skills.py"], {"tests", "distribution"}),
        (["packages/a13n-harness-ui/pyproject.toml"], {"tests", "distribution"}),
        (["packages/a13n-envd-client/a13n_envd_client/eip/client.py"], {"tests", "distribution"}),
        (["packages/a13n-logging/a13n_logging/__init__.py"], {"tests", "distribution"}),
        (["packages/a13n-harness/a13n_harness/types.py"], {"tests", "distribution"}),
        (["packages/a13n-service/pyproject.toml"], {"tests", "distribution"}),
        (["uv.lock"], {"tests", "distribution"}),
        (["conftest.py"], {"tests", "distribution"}),
        (["docs/a13n-harness-ui/configuration.md"], {"tests", "distribution"}),
        (["docs/a13n-harness-ui/meta.json"], {"tests", "distribution"}),
        (["scripts/export-a13n-harness-ui-openapi.py"], {"distribution"}),
        (["scripts/tests/test_prepare_release_version.py"], {"distribution"}),
        (["scripts/check_a13n_harness_ui_distribution.py"], {"tests", "distribution"}),
        ([".github/workflows/ci-a13n-harness-ui.yml"], {"tests", "frontend", "distribution"}),
        (["scripts/tests/test_harness_ui_ci_workflow.py"], {"tests", "frontend", "distribution"}),
        (
            ["packages/a13n-harness-ui/tests/test_cli_interactions.py", "frontend/apps/a13n-console/src/app.tsx"],
            {"tests", "frontend"},
        ),
        (["docs/a13n-service/index.md"], set()),
    ],
)
def test_ui_ci_selects_only_affected_jobs(paths: list[str], expected: set[str]) -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    filters = yaml.safe_load(workflow["jobs"]["changes"]["steps"][1]["with"]["filters"])
    assert all(not pattern.startswith("!") for patterns in filters.values() for pattern in patterns)
    actual = {
        name
        for name, patterns in filters.items()
        if any(Path(path).full_match(pattern) for path in paths for pattern in patterns)
    }
    assert actual == expected
    for event in ("pull_request", "push"):
        assert any(
            Path(path).full_match(pattern) for path in paths for pattern in workflow[True][event]["paths"]
        ) == bool(expected)


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
    assert "packages/a13n-harness-ui/tests/test_host_files.py" in selections
    assert "packages/a13n-harness-ui/tests/test_host_git.py" in selections
    assert "packages/a13n-harness-ui/tests/test_configuration_mutation.py" in selections
    full = by_name["Test full UI suite on Windows"]
    assert full["if"] == "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
    assert shlex.split(full["run"])[-1] == "packages/a13n-harness-ui/tests"
    lifecycle = by_name["Test native command lifecycle"]
    assert "if" not in lifecycle
    for name in ("test_direct_local.py", "test_direct_local_processes.py"):
        assert f"packages/a13n-environment/tests/{name}" in shlex.split(lifecycle["run"])


def test_bundled_documentation_does_not_trigger_image_publication() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/images.yml").read_text())
    path = Path("docs/a13n-harness-ui/configuration.md")
    assert not any(path.full_match(pattern) for pattern in workflow[True]["push"]["paths"])
    step = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
    filters = yaml.safe_load(step["with"]["filters"])
    assert "harness_ui" not in filters
    assert not any(path.full_match(pattern) for patterns in filters.values() for pattern in patterns)
