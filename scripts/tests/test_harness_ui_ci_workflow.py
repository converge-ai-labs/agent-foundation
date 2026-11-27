from __future__ import annotations

import ast
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github/workflows/ci-a13n-harness-ui.yml"


@pytest.mark.parametrize(
    ("component", "selected", "result"),
    [
        ("CHECKS", True, "success"),
        ("TESTS", True, "failure"),
        ("FRONTEND", True, "cancelled"),
        ("WEBUI", True, "skipped"),
        ("WEBUI", True, "failure"),
        ("WEBUI", True, "cancelled"),
        ("DISTRIBUTION", False, "skipped"),
        ("TESTS", False, "success"),
    ],
)
def test_linux_gate_requires_exact_selected_results(component: str, selected: bool, result: str) -> None:
    gate_job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["linux"]
    assert "github.event_name != 'schedule'" not in gate_job["if"]
    gate = gate_job["steps"][0]
    env = {"CHANGES_RESULT": "success"}
    for name in ("CHECKS", "TESTS", "FRONTEND", "WEBUI", "DISTRIBUTION"):
        env[f"{name}_RESULT"] = "success"
        env[f"{name}_SELECTED"] = "true"
        assert gate["env"][f"{name}_SELECTED"] == (
            "${{ github.event_name == 'schedule' || github.event_name == 'workflow_dispatch' || needs.changes.outputs."
            + ({"CHECKS": "tests", "WEBUI": "distribution"}.get(name, name.lower()))
            + " == 'true' }}"
        )
        assert gate["env"][f"{name}_RESULT"] == "${{ needs." + name.lower() + ".result }}"
    env[f"{component}_RESULT"] = result
    env[f"{component}_SELECTED"] = str(selected).lower()
    completed = subprocess.run(["bash", "-e", "-c", gate["run"]], env=env, check=False)
    assert (completed.returncode == 0) == (result == ("success" if selected else "skipped"))


@pytest.mark.parametrize("result", ["failure", ""])
def test_linux_gate_rejects_failed_classification(result: str) -> None:
    gate = yaml.safe_load(WORKFLOW.read_text())["jobs"]["linux"]["steps"][0]
    assert gate["env"]["CHANGES_RESULT"] == "${{ needs.changes.result }}"
    completed = subprocess.run(["bash", "-e", "-c", gate["run"]], env={"CHANGES_RESULT": result}, check=False)
    assert completed.returncode != 0


@pytest.mark.parametrize(
    "paths,expected",
    [
        (["frontend/apps/a13n-console/src/features/traces/detail.tsx"], {"frontend"}),
        (["proto/a13n-service/openapi.json"], {"frontend"}),
        (["sdk/typescript/src/client.ts"], set()),
        (["frontend/apps/a13n-harness-ui/src/shell/workbench.tsx"], {"distribution"}),
        (["frontend/packages/a13n-ui/src/components/button.tsx"], {"frontend", "distribution"}),
        (["frontend/pnpm-lock.yaml"], {"frontend", "distribution"}),
        ([".github/ui-test-durations.json"], {"tests"}),
        (["packages/a13n-harness-ui/tests/test_cli_interactions.py"], {"tests"}),
        (["packages/a13n-harness/tests/providers_environment/test_direct_local.py"], {"tests"}),
        (["packages/a13n-harness/tests/test_agent.py"], set()),
        (["packages/a13n-harness/tests/conftest.py"], {"tests"}),
        (["packages/a13n-harness-ui/a13n_harness_ui/app.py"], {"tests", "distribution"}),
        (["packages/a13n-harness-ui/README.md"], {"distribution"}),
        (["packages/a13n-harness/a13n_harness/types.py"], {"tests", "distribution"}),
        (["uv.lock"], {"tests", "distribution"}),
        (["docs/a13n-harness-ui/configuration.md"], {"tests", "distribution"}),
        (["scripts/export-a13n-harness-ui-openapi.py"], {"distribution"}),
        ([".github/workflows/ci-a13n-harness-ui.yml"], {"tests", "frontend", "distribution"}),
        (["scripts/tests/test_harness_ui_ci_workflow.py"], set()),
        (["packages/a13n-harness/README.md"], set()),
        (
            ["packages/a13n-harness-ui/tests/test_cli_interactions.py", "frontend/apps/a13n-console/src/app.tsx"],
            {"tests", "frontend"},
        ),
        (["docs/a13n-service/index.md"], set()),
        (["release-dependencies.toml"], set()),
    ],
)
def test_ui_ci_selects_only_affected_jobs(paths: list[str], expected: set[str]) -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    filter_step = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
    filters = yaml.safe_load(filter_step["with"]["filters"])
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
    assert "-n 4 --dist loadgroup" in native["run"]
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
    assert lifecycle["if"] == "matrix.shard == 1"
    assert "-n 4 --dist load " in lifecycle["run"]
    for name in ("test_direct_local.py", "test_direct_local_processes.py"):
        assert f"packages/a13n-harness/tests/providers_environment/{name}" in shlex.split(lifecycle["run"])


def test_python_checks_run_beside_the_test_shards_and_remain_required() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    checks = jobs["checks"]
    assert checks["needs"] == jobs["tests"]["needs"] == "changes"
    assert checks["if"] == jobs["tests"]["if"]
    assert "checks" in jobs["linux"]["needs"]
    steps = {step["name"]: step for step in checks["steps"]}
    assert "if" not in steps["Type-check UI"]
    assert "if" not in steps["Test native command lifecycle"]
    assert "packages/a13n-harness-ui/a13n_harness_ui" in steps["Type-check UI"]["run"]
    assert "scripts/release_version.py" in steps["Type-check UI"]["run"]
    for name in ("test_direct_local.py", "test_direct_local_processes.py"):
        assert (
            f"packages/a13n-harness/tests/providers_environment/{name}" in steps["Test native command lifecycle"]["run"]
        )
    assert all(
        step["name"] not in {"Type-check UI", "Test native command lifecycle"} for step in jobs["tests"]["steps"]
    )


def test_linux_python_matrix_runs_every_duration_balanced_shard() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    tests = jobs["tests"]
    strategy = tests["strategy"]
    shards = strategy["matrix"]["shard"]
    assert len(shards) > 1
    assert shards == list(range(1, len(shards) + 1))
    assert "tests" in jobs["linux"]["needs"]
    assert strategy["fail-fast"] is False
    test = next(step for step in tests["steps"] if step["name"] == "Test UI")
    assert f"--shard ${{{{ matrix.shard }}}} {len(shards)} --durations .github/ui-test-durations.json" in test["run"]
    assert test["run"].endswith("packages/a13n-harness-ui/tests")


def test_webui_matrix_requires_both_unit_shards_and_the_protocol_project() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    webui = jobs["webui"]
    assert "webui" in jobs["linux"]["needs"]
    assert webui["needs"] == jobs["distribution"]["needs"] == "changes"
    assert webui["if"] == jobs["distribution"]["if"]
    assert webui["strategy"]["fail-fast"] is False
    assert webui["strategy"]["matrix"]["include"] == [
        {"project": "unit", "shard": "1/2"},
        {"project": "unit", "shard": "2/2"},
        {"project": "protocol"},
    ]
    steps = {step["name"]: step for step in webui["steps"]}
    for name in (
        "Set up Python",
        "Set up uv",
        "Synchronize UI test environment",
        "Prepare bundled configuration Skill",
    ):
        assert steps[name]["if"] == "matrix.project == 'protocol'"
    assert "--project=${{ matrix.project }}" in steps["Test WebUI"]["run"]
    assert "matrix.shard && format('--shard={0}', matrix.shard)" in steps["Test WebUI"]["run"]
    assert "strategy.job-index" in steps["Retain WebUI test reports"]["with"]["name"]
