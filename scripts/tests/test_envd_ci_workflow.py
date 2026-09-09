from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).parents[2] / ".github/workflows/ci-a13n-envd.yml"


def test_protocol_and_platform_checks_run_independently() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    for name in ("protocol", "client", "daemon"):
        assert jobs[name]["needs"] == "changes"
        assert "needs.protocol.result" not in jobs[name]["if"]
    assert set(jobs["validation"]["needs"]) == {"changes", "protocol", "client", "daemon"}
    assert jobs["validation"]["if"].startswith("always() &&")


@pytest.mark.parametrize("component", ["PROTOCOL", "CLIENT", "DAEMON"])
@pytest.mark.parametrize("selected", [True, False])
@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped"])
def test_envd_gate_requires_exact_selected_results(component: str, selected: bool, result: str) -> None:
    gate = yaml.safe_load(WORKFLOW.read_text())["jobs"]["validation"]["steps"][0]
    env = {"CHANGES_RESULT": "success"}
    for name in ("PROTOCOL", "CLIENT", "DAEMON"):
        env[f"{name}_RESULT"] = "success"
        env[f"{name}_SELECTED"] = "true"
    env[f"{component}_RESULT"] = result
    env[f"{component}_SELECTED"] = str(selected).lower()
    completed = subprocess.run(["bash", "-e", "-c", gate["run"]], env=env, check=False)
    assert (completed.returncode == 0) == (result == ("success" if selected else "skipped"))


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
def test_envd_gate_rejects_failed_classification(result: str) -> None:
    gate = yaml.safe_load(WORKFLOW.read_text())["jobs"]["validation"]["steps"][0]
    completed = subprocess.run(["bash", "-e", "-c", gate["run"]], env={"CHANGES_RESULT": result}, check=False)
    assert completed.returncode != 0
