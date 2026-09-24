from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github/workflows"


@pytest.mark.parametrize(
    "path,selected",
    [
        ("packages/a13n-envd-client/a13n_envd_client/requester.py", {"client", "protocol"}),
        ("packages/a13n-envd-client/a13n_envd_client/file_transfer.py", {"client", "protocol"}),
        ("packages/a13n-envd-client/tests/eip/test_device_sessions.py", {"client", "protocol"}),
        ("packages/a13n-harness/a13n_harness/providers/environment/remote_envd/attachment.py", {"protocol"}),
        ("packages/a13n-harness/tests/providers_environment/test_remote_envd_e2e.py", {"protocol"}),
        ("examples/environment-provider/tests/test_stdio.py", {"protocol"}),
        ("crates/a13n-envd/src/transfer.rs", {"daemon", "protocol"}),
        ("Makefile", {"client", "daemon", "protocol"}),
        ("conftest.py", {"client", "daemon", "protocol"}),
        ("scripts/tests/test_envd_ci_workflow.py", {"client", "daemon", "protocol"}),
        ("docs/a13n-service/index.md", set()),
    ],
)
def test_envd_runtime_inputs_select_native_protocol(path: str, selected: set[str]) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-envd.yml").read_text())
    classifier = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
    filters = yaml.safe_load(classifier["with"]["filters"])
    assert {
        name for name, patterns in filters.items() if any(Path(path).full_match(pattern) for pattern in patterns)
    } == selected
    for event in ("pull_request", "push"):
        assert any(Path(path).full_match(pattern) for pattern in workflow[True][event]["paths"]) == bool(selected)


@pytest.mark.parametrize(
    "path,selected",
    [
        ("packages/a13n-envd-client/a13n_envd_client/requester.py", True),
        ("packages/a13n-logging/a13n_logging/__init__.py", True),
        ("packages/a13n-envd-client/pyproject.toml", True),
        ("packages/a13n-logging/pyproject.toml", True),
        ("packages/a13n-harness/tests/test_environment_core.py", True),
        ("conftest.py", True),
        ("packages/a13n-logging/tests/test_logging.py", False),
        ("docs/index.md", False),
    ],
)
def test_harness_selects_direct_dependency_inputs(path: str, selected: bool) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-harness.yml").read_text())
    for event in ("pull_request", "push"):
        assert any(Path(path).full_match(pattern) for pattern in workflow[True][event]["paths"]) == selected


def test_makefile_selects_all_container_commands() -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-containers.yml").read_text())
    path = Path("Makefile")
    assert any(path.full_match(pattern) for pattern in workflow[True]["pull_request"]["paths"])
    classifier = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
    filters = yaml.safe_load(classifier["with"]["filters"])
    assert all(any(path.full_match(pattern) for pattern in patterns) for patterns in filters.values())


def test_service_ci_runs_service_tests_and_checks() -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service.yml").read_text())
    jobs = workflow["jobs"]
    assert {entry["name"] for entry in jobs["validation"]["strategy"]["matrix"]["include"]} == {"checks", "tests"}
    steps = jobs["validation"]["steps"]
    test = next(step for step in steps if step["name"] == "Test a13n Service")
    assert "packages/a13n-service/tests" in test["run"]
    assert any(step.get("run") == "make service-boundaries" for step in steps)
    assert any(step.get("run") == "make dev-state-check" for step in steps)
    assert jobs["python"]["needs"] == "validation"


@pytest.mark.parametrize(
    "cases,accepted", [("", False), ("<testcase/>", True), ("<testcase><skipped/></testcase>", False)]
)
def test_service_tests_reject_empty_or_skipped_execution(tmp_path: Path, cases: str, accepted: bool) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["validation"]["steps"]
        if step["name"] == "Require Service execution without skips"
    )
    reports = tmp_path / "test-results"
    reports.mkdir()
    (reports / "service.xml").write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
    result = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp_path, capture_output=True, check=False)
    assert (result.returncode == 0) == accepted


@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped"])
def test_service_gate_requires_every_matrix_member(result: str) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service.yml").read_text())
    gate = workflow["jobs"]["python"]
    assert "always()" in gate["if"]
    step = gate["steps"][0]
    assert step["env"]["VALIDATION_RESULT"] == "${{ needs.validation.result }}"
    completed = subprocess.run(["bash", "-e", "-c", step["run"]], env={"VALIDATION_RESULT": result}, check=False)
    assert (completed.returncode == 0) == (result == "success")


@pytest.mark.parametrize(
    "workflow_name,job",
    [
        ("ci-a13n-envd.yml", "protocol"),
        ("ci-a13n-envd.yml", "client"),
        ("ci-a13n-envd.yml", "daemon"),
        ("ci-a13n-harness.yml", "harness"),
        ("ci-a13n-service.yml", "validation"),
        ("ci-a13n-stream-protocol.yml", "protocol"),
        ("ci-a13n-harness-ui.yml", "tests"),
        ("ci-a13n-harness-ui.yml", "frontend"),
        ("ci-a13n-harness-ui.yml", "distribution"),
        ("ci-a13n-harness-ui.yml", "windows"),
    ],
)
def test_component_jobs_retain_scoped_failure_reports(workflow_name: str, job: str) -> None:
    steps = yaml.safe_load((WORKFLOWS / workflow_name).read_text())["jobs"][job]["steps"]
    uploads = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")]
    assert uploads
    for upload in uploads:
        assert "always()" in upload["if"]
        assert "test-results" in upload["with"]["path"]
        assert 0 < upload["with"]["retention-days"] <= 14
    commands = "\n".join(str(step.get("run", "")) + str(step.get("env", {})) for step in steps)
    assert any(
        marker in commands
        for marker in ("junitxml", "--reporter=junit", "test-results/native.log", "EIP_TEST_REPORT_DIR")
    )


def test_failure_report_contains_fixture_phase_and_captured_output(tmp_path: Path) -> None:
    source = tmp_path / "test_failure.py"
    source.write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "def resource():\n"
        "    print('setup witness')\n"
        "    yield\n"
        "    print('teardown witness')\n"
        "    raise RuntimeError('cleanup failure')\n"
        "def test_failure(resource):\n"
        "    print('call witness')\n"
        "    assert False, 'primary failure'\n"
    )
    report = tmp_path / "failure.xml"
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(ROOT / "pyproject.toml"), str(source), f"--junitxml={report}"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    suite = ET.parse(report).getroot().find("testsuite")
    assert suite is not None
    assert suite.get("failures") == "1" and suite.get("errors") == "1"
    contents = report.read_text()
    for witness in ("setup witness", "call witness", "teardown witness", "primary failure", "cleanup failure"):
        assert witness in contents
