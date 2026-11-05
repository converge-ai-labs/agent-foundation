from __future__ import annotations

import ast
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github/workflows/ci-a13n-harness-ui.yml"


@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped", ""])
@pytest.mark.parametrize("selected", [True, False])
@pytest.mark.parametrize("component", ["TESTS", "FRONTEND", "WEBUI", "DISTRIBUTION"])
def test_linux_gate_requires_exact_selected_results(component: str, selected: bool, result: str) -> None:
    gate = yaml.safe_load(WORKFLOW.read_text())["jobs"]["linux"]["steps"][0]
    env = {"CHANGES_RESULT": "success"}
    for name in ("TESTS", "FRONTEND", "WEBUI", "DISTRIBUTION"):
        env[f"{name}_RESULT"] = "success"
        env[f"{name}_SELECTED"] = "true"
        assert gate["env"][f"{name}_SELECTED"] == (
            "${{ github.event_name == 'workflow_dispatch' || needs.changes.outputs."
            + ("distribution" if name == "WEBUI" else name.lower())
            + " == 'true' }}"
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
        ([".github/ui-test-durations.json"], {"tests"}),
        (["packages/a13n-harness-ui/tests/test_cli_interactions.py"], {"tests"}),
        (["packages/a13n-harness-ui/tests/conftest.py"], {"tests"}),
        (["packages/a13n-harness/tests/providers_environment/test_direct_local.py"], {"tests"}),
        (["packages/a13n-harness/tests/providers_environment/test_direct_local_processes.py"], {"tests"}),
        (["packages/a13n-harness/tests/test_agent.py"], set()),
        (["packages/a13n-harness/tests/conftest.py"], {"tests"}),
        (["packages/a13n-harness/tests/providers_environment/conftest.py"], {"tests"}),
        (["packages/a13n-envd-client/tests/conftest.py"], set()),
        (["packages/a13n-stream-protocol/tests/test_projection.py"], set()),
        (["packages/a13n-logging/tests/test_logging.py"], set()),
        (["packages/a13n-harness-ui/a13n_harness_ui/app.py"], {"tests", "distribution"}),
        (["packages/a13n-harness-ui/README.md"], {"distribution"}),
        (["packages/a13n-harness-ui/LICENSE"], {"distribution"}),
        (
            ["packages/a13n-harness-ui/README.md", "packages/a13n-harness-ui/a13n_harness_ui/app.py"],
            {"tests", "distribution"},
        ),
        (["packages/a13n-harness-ui/hatch_build.py"], {"tests", "distribution"}),
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
        (["scripts/tests/test_prepare_release_version.py"], set()),
        (["scripts/check_a13n_harness_ui_distribution.py"], {"tests", "distribution"}),
        ([".github/workflows/ci-a13n-harness-ui.yml"], {"tests", "frontend", "distribution"}),
        (["scripts/tests/test_harness_ui_ci_workflow.py"], set()),
        (["packages/a13n-harness/README.md"], set()),
        (["packages/a13n-envd-client/README.md"], set()),
        (["packages/a13n-stream-protocol/README.md"], set()),
        (["packages/a13n-logging/README.md"], set()),
        (["packages/a13n-harness/README.md", "packages/a13n-harness/pyproject.toml"], {"tests", "distribution"}),
        (["packages/a13n-harness/hatch_build.py"], {"tests", "distribution"}),
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
    assert lifecycle["if"] == "matrix.shard == 1"
    for name in ("test_direct_local.py", "test_direct_local_processes.py"):
        assert f"packages/a13n-harness/tests/providers_environment/{name}" in shlex.split(lifecycle["run"])
