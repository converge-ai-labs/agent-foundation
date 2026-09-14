from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("component", ["a13n-harness", "a13n-harness-ui"])
def test_release_publishes_after_build_without_validation_jobs(component: str) -> None:
    workflow = ROOT / f".github/workflows/release-{component}.yml"
    jobs = yaml.safe_load(workflow.read_text())["jobs"]
    expected_jobs = {"build-python", "publish-python", "create-release"}
    release_needs = {"build-python", "publish-python"}
    if component == "a13n-harness-ui":
        expected_jobs.add("publish-image")
        release_needs.add("publish-image")
        assert set(jobs["publish-image"]["needs"]) == {"build-python", "publish-python"}
    assert set(jobs) == expected_jobs
    assert jobs["publish-python"]["needs"] == "build-python"
    assert set(jobs["create-release"]["needs"]) == release_needs
    assert "if" not in jobs["publish-python"]
    expected_environment = "harness-pypi" if component == "a13n-harness" else "agent-ui-pypi"
    assert jobs["publish-python"]["environment"] == expected_environment
    for job in jobs.values():
        assert "continue-on-error" not in job
        for step in job["steps"]:
            assert "check-release-version.py" not in step.get("run", "")
            assert not step["name"].startswith("Verify")


@pytest.mark.parametrize("component", ["a13n-harness", "a13n-harness-ui"])
def test_release_build_targets_only_build_prepared_distributions(component: str) -> None:
    result = subprocess.run(
        ["make", "--dry-run", f"{component}-release-build"], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "uv build" in result.stdout
    assert "scripts/check-" not in result.stdout
    assert "pytest" not in result.stdout


@pytest.mark.parametrize("component", ["a13n-harness", "a13n-harness-ui"])
def test_release_shell_steps_parse(component: str) -> None:
    workflow = ROOT / f".github/workflows/release-{component}.yml"
    jobs = yaml.safe_load(workflow.read_text())["jobs"]
    for job in jobs.values():
        for step in job["steps"]:
            if "run" in step:
                result = subprocess.run(["bash", "-n"], input=step["run"], capture_output=True, text=True)
                assert result.returncode == 0, f"{step['name']}: {result.stderr}"
