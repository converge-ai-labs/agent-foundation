"""The automatic gate runs only the reviewed smoke selection using job services."""

import os
import subprocess
import xml.etree.ElementTree as ET

import pytest
import yaml

from ..ci import REPOSITORY
from ..infrastructure.local_storage import RUSTFS_IMAGE


def workflow():
    return yaml.safe_load((REPOSITORY / ".github/workflows/ci-live-tests.yml").read_text())


def test_smoke_uses_job_services_and_does_not_repeat_native_builds():
    jobs = workflow()["jobs"]
    assert set(jobs["smoke"]["services"]) == {"postgres", "redis", "rustfs"}
    assert jobs["smoke"]["services"]["rustfs"]["image"] == RUSTFS_IMAGE
    commands = "\n".join(step.get("run", "") for step in jobs["smoke"]["steps"])
    assert "suite=smoke" in commands and "--infrastructure=external" in commands
    assert "--smoke-group=$SMOKE_GROUP" in commands
    assert "--workers=$PYTEST_WORKERS" in commands
    assert "--infrastructure-config=$INFRA_CONFIG" in commands
    assert "environment-build" not in commands and "docker build" not in commands
    assert "needs" not in jobs["smoke"], "Offline validation must not delay smoke startup"
    assert set(jobs["validation"]["needs"]) == {"support", "smoke"}
    strategy = jobs["smoke"]["strategy"]
    assert strategy["fail-fast"] is False and strategy["max-parallel"] == 3
    groups = strategy["matrix"]["include"]
    assert [(group["group"], group["cases"]) for group in groups] == [
        ("core", 20),
        ("round-two", 4),
        ("management", 10),
    ]
    assert {group["group"]: group["workers"] for group in groups} == {"core": 2, "round-two": 1, "management": 1}
    assert jobs["smoke"]["env"] == {
        "SMOKE_GROUP": "${{ matrix.group }}",
        "EXPECTED_TESTS": "${{ matrix.cases }}",
        "PYTEST_WORKERS": "${{ matrix.workers }}",
    }
    artifact = next(
        step for step in jobs["smoke"]["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert artifact["with"]["name"] == "live-smoke-${{ matrix.group }}"


@pytest.mark.parametrize("group,expected", [("core", 20), ("round-two", 4), ("management", 10)])
@pytest.mark.parametrize(
    "missing,failures,errors,skipped", [(0, 0, 0, 0), (1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)]
)
def test_ci_summary_rejects_partial_or_nonpassing_results(
    tmp_path, group, expected, missing, failures, errors, skipped
):
    tests = expected - missing
    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root,
        "testsuite",
        tests=str(tests),
        failures=str(failures),
        errors=str(errors),
        skipped=str(skipped),
        time="1.5",
    )
    for index in range(tests):
        ET.SubElement(suite, "testcase", classname="smoke", name=f"case-{index}", time="0.01")
    report = tmp_path / "smoke.xml"
    ET.ElementTree(root).write(report)
    summary = tmp_path / "summary.md"
    step = next(step for step in workflow()["jobs"]["smoke"]["steps"] if step.get("name", "").startswith("Summarize"))
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={
            **os.environ,
            "REPORT": str(report),
            "GITHUB_STEP_SUMMARY": str(summary),
            "SMOKE_GROUP": group,
            "EXPECTED_TESTS": str(expected),
        },
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == ((tests, failures, errors, skipped) == (expected, 0, 0, 0))
    assert f"Live smoke ({group})" in summary.read_text()
    assert "pytest wall time: 1.50s" in summary.read_text()


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
@pytest.mark.parametrize("job", ["SUPPORT", "SMOKE"])
def test_live_gate_rejects_every_nonpassing_dependency(job, result):
    step = workflow()["jobs"]["validation"]["steps"][0]
    env = {"SUPPORT_RESULT": "success", "SMOKE_RESULT": "success", f"{job}_RESULT": result}
    assert subprocess.run(["bash", "-e", "-c", step["run"]], env=env).returncode != 0
