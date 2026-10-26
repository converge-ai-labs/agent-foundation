from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from statistics import median

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
        ("docs/index.mdx", False),
    ],
)
def test_harness_selects_direct_dependency_inputs(path: str, selected: bool) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-harness.yml").read_text())
    for event in ("pull_request", "push"):
        assert any(Path(path).full_match(pattern) for pattern in workflow[True][event]["paths"]) == selected


@pytest.mark.parametrize(
    "workflow_name,dependencies",
    [
        ("ci-a13n-service.yml", ("a13n-harness", "a13n-envd-client", "a13n-stream-protocol")),
        ("ci-examples.yml", ("a13n-harness",)),
        ("ci-a13n-stream-protocol.yml", ("a13n-harness", "a13n-envd-client", "a13n-logging")),
        (
            "ci-a13n-harness-ui.yml",
            ("a13n-harness", "a13n-envd-client", "a13n-stream-protocol", "a13n-logging"),
        ),
        (
            "ci-a13n-service-e2e.yml",
            ("a13n-service", "a13n-harness", "a13n-envd-client", "a13n-stream-protocol", "a13n-logging"),
        ),
    ],
)
def test_consumers_retain_runtime_and_build_inputs_without_repeating_dependency_tests(
    workflow_name: str, dependencies: tuple[str, ...]
) -> None:
    workflow = yaml.safe_load((WORKFLOWS / workflow_name).read_text())
    for package in dependencies:
        for relative, selected in (
            (f"{package.replace('-', '_')}/nested/module.py", True),
            (f"{package.replace('-', '_')}/resources/schema.json", True),
            ("pyproject.toml", True),
            ("hatch_build.py", True),
            ("conftest.py", True),
            ("tests/test_unit.py", False),
            ("tests/conftest.py", False),
            ("tests/fixtures/input.json", False),
        ):
            path = Path("packages") / package / relative
            if (
                workflow_name == "ci-a13n-harness-ui.yml"
                and package == "a13n-harness"
                and relative == "tests/conftest.py"
            ):
                selected = True  # The explicit native lifecycle tests use the Harness fixtures.
            for event in ("pull_request", "push"):
                assert any(path.full_match(pattern) for pattern in workflow[True][event]["paths"]) == selected
    for event in ("pull_request", "push"):
        for path in ("pyproject.toml", "uv.lock"):
            assert any(Path(path).full_match(pattern) for pattern in workflow[True][event]["paths"])
        if workflow_name != "ci-examples.yml":
            path = "conftest.py"
            assert any(Path(path).full_match(pattern) for pattern in workflow[True][event]["paths"])


def test_tooling_suites_keep_one_ci_owner_and_release_manifest_inputs() -> None:
    owners = {
        "test_envd_ci_workflow.py": "ci.yml",
        "test_image_workflows.py": "ci.yml",
        "test_harness_ui_ci_workflow.py": "ci.yml",
        "test_prepare_release_version.py": "ci-automation.yml",
    }
    for test, owner in owners.items():
        executions = []
        for name in ("ci.yml", "ci-automation.yml", "ci-a13n-harness-ui.yml"):
            workflow = yaml.safe_load((WORKFLOWS / name).read_text())
            for job in workflow["jobs"].values():
                for step in job["steps"]:
                    if f"scripts/tests/{test}" in step.get("run", ""):
                        executions.append(name)
        assert executions == [owner]
    automation = yaml.safe_load((WORKFLOWS / "ci-automation.yml").read_text())
    for event in ("pull_request", "push"):
        for path in (
            *ROOT.glob("packages/*/pyproject.toml"),
            ROOT / "pyproject.toml",
            ROOT / "uv.lock",
            ROOT / "Cargo.toml",
            ROOT / "Cargo.lock",
            ROOT / "crates/a13n-envd/Cargo.toml",
            ROOT / "scripts/tests/test_prepare_release_version.py",
        ):
            assert any(path.relative_to(ROOT).full_match(pattern) for pattern in automation[True][event]["paths"])


@pytest.mark.parametrize(
    "path,selected",
    [
        ("Makefile", False),
        ("examples/mcp-apps/package-lock.json", True),
        ("examples/mcp-apps/app.js", True),
    ],
)
def test_examples_ci_selects_build_inputs_and_prepares_node(path: str, selected: bool) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-examples.yml").read_text())
    for event in ("pull_request", "push"):
        assert any(Path(path).full_match(pattern) for pattern in workflow[True][event]["paths"]) == selected
    steps = workflow["jobs"]["examples"]["steps"]
    node = next(step for step in steps if step.get("uses", "").startswith("actions/setup-node@"))
    gate = next(step for step in steps if step.get("run") == "make examples-check-all")
    assert node["with"]["node-version"] == "24"
    assert node["with"]["cache-dependency-path"] == "examples/mcp-apps/package-lock.json"
    assert steps.index(node) < steps.index(gate)


def test_makefile_selects_all_container_commands() -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-containers.yml").read_text())
    path = Path("Makefile")
    assert any(path.full_match(pattern) for pattern in workflow[True]["pull_request"]["paths"])
    classifier = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
    filters = yaml.safe_load(classifier["with"]["filters"])
    assert all(any(path.full_match(pattern) for pattern in patterns) for patterns in filters.values())


@pytest.mark.parametrize("path", ["docs/a13n-service/mcp.md", "docs/a13n-service/mcp.zh-CN.md"])
def test_bundled_service_docs_select_package_and_image_builds(path: str) -> None:
    for name in ("ci-a13n-service.yml", "ci-containers.yml", "images.yml"):
        workflow = yaml.safe_load((WORKFLOWS / name).read_text())
        for event in ("pull_request", "push"):
            if event in workflow[True]:
                assert any(Path(path).full_match(pattern) for pattern in workflow[True][event]["paths"])
        if name != "ci-a13n-service.yml":
            classifier = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
            filters = yaml.safe_load(classifier["with"]["filters"])
            assert {key for key, patterns in filters.items() if any(Path(path).full_match(p) for p in patterns)} == {
                "service"
            }


def _service_matrix(workflow: dict, tests_changed: str, tmp_path: Path, *, root: Path = ROOT) -> list[dict[str, str]]:
    step = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "matrix")
    output = tmp_path / "output"
    output.write_text("")
    subprocess.run(
        ["bash", "-c", step["run"]],
        env={**os.environ, "TESTS_CHANGED": tests_changed, "GITHUB_OUTPUT": str(output)},
        cwd=root,
        check=True,
    )
    assert output.read_text().startswith("matrix=")
    return json.loads(output.read_text().removeprefix("matrix="))


def test_service_ci_runs_service_tests_and_checks(tmp_path: Path) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service.yml").read_text())
    jobs = workflow["jobs"]
    matrix = _service_matrix(workflow, "true", tmp_path)
    assert [entry["name"] for entry in matrix] == ["checks", "tests-1", "tests-2"]
    shards = [entry["targets"].splitlines() for entry in matrix[1:]]
    assert all(shards)
    assert not set(shards[0]) & set(shards[1])
    root = ROOT / "packages/a13n-service/tests"
    expected = {
        str(path.relative_to(ROOT))
        for path in root.rglob("*.py")
        if (path.name.startswith("test_") or path.name.endswith("_test.py"))
        and path not in {root / "test_environments_envd.py", root / "test_monitoring_collectors_live.py"}
        and root / "environment_providers" not in path.parents
    }
    assert set(shards[0]) | set(shards[1]) == expected
    durations = json.loads((ROOT / ".github/service-test-durations.json").read_text())
    fallback = median(durations[path] for path in expected if path in durations)
    totals = [sum(durations.get(path, fallback) for path in shard) for shard in shards]
    assert abs(totals[0] - totals[1]) < sum(totals) * 0.01
    assert matrix == _service_matrix(workflow, "true", tmp_path)
    for event in ("pull_request", "push"):
        assert ".github/service-test-durations.json" in workflow[True][event]["paths"]
    assert {entry["runner"] for entry in matrix} <= {"ubuntu-latest", "ubuntu-24.04"}
    assert jobs["validation"]["strategy"]["matrix"]["include"] == "${{ fromJSON(needs.changes.outputs.matrix) }}"
    steps = jobs["validation"]["steps"]
    test = next(step for step in steps if step["name"] == "Test a13n Service")
    assert "-n 3 --dist loadgroup" in test["run"]
    assert test["if"] == "startsWith(matrix.name, 'tests-')"
    assert test["env"]["TEST_TARGETS"] == "${{ matrix.targets }}"
    require_execution = next(step for step in steps if step["name"] == "Require Service execution without skips")
    assert require_execution["if"] == test["if"]
    providers = next(step for step in steps if step["name"] == "Test native Environment management drivers")
    assert providers["if"] == "matrix.name == 'tests-1'"
    assert "packages/a13n-service/tests/environment_providers" in providers["run"]
    reports = next(step for step in steps if step["name"] == "Retain Service test reports")
    assert reports["if"] == "always()"
    assert reports["with"]["name"] == "service-${{ matrix.name }}-${{ github.run_attempt }}"
    assert jobs["validation"]["strategy"]["fail-fast"] is False
    assert any(step.get("run") == "make service-boundaries" for step in steps)
    assert any(step.get("run") == "make dev-state-check" for step in steps)
    assert jobs["python"]["needs"] == ["changes", "validation"]


def test_service_shards_include_new_nested_files_and_pass_paths_literally(tmp_path: Path) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service.yml").read_text())
    root = tmp_path / "packages/a13n-service/tests"
    included = ["test_first.py", "nested/test_second.py", "nested/third_test.py", "test_space $(false).py"]
    excluded = [
        "conftest.py",
        "helper.py",
        "test_environments_envd.py",
        "test_monitoring_collectors_live.py",
        "environment_providers/test_native.py",
    ]
    for name in included + excluded:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/service-test-durations.json").write_text("{}")
    matrix = _service_matrix(workflow, "true", tmp_path, root=tmp_path)
    shards = [entry["targets"].splitlines() for entry in matrix[1:]]
    expected = sorted(str((root / name).relative_to(tmp_path)) for name in included)
    assert shards == [expected[::2], expected[1::2]]

    # Execute the actual workflow command with a fake uv to check shell argument boundaries.
    executable = tmp_path / "uv"
    executable.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    executable.chmod(0o755)
    step = next(step for step in workflow["jobs"]["validation"]["steps"] if step["name"] == "Test a13n Service")
    for entry, targets in zip(matrix[1:], shards, strict=True):
        result = subprocess.run(
            ["bash", "-e", "-c", step["run"]],
            env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", "TEST_TARGETS": entry["targets"]},
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        assert json.loads(result.stdout)[-len(targets) :] == targets


def test_service_shards_balance_heavy_files_and_include_unmeasured_files(tmp_path: Path) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service.yml").read_text())
    root = tmp_path / "packages/a13n-service/tests"
    root.mkdir(parents=True)
    # Alternating alphabetically would put both heavy files on the same runner.
    weights = {"test_a.py": 100, "test_b.py": 10, "test_c.py": 90, "test_d.py": 10}
    for name in [*weights, "test_new.py"]:
        (root / name).touch()
    durations = {f"packages/a13n-service/tests/{name}": value for name, value in weights.items()}
    durations["packages/a13n-service/tests/test_removed.py"] = 10000
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/service-test-durations.json").write_text(json.dumps(durations))
    matrix = _service_matrix(workflow, "true", tmp_path, root=tmp_path)
    shards = [[Path(path).name for path in entry["targets"].splitlines()] for entry in matrix[1:]]
    assert sorted(name for shard in shards for name in shard) == sorted([*weights, "test_new.py"])
    assert not any({"test_a.py", "test_c.py"} <= set(shard) for shard in shards)
    # The unknown file receives the current files' median (50), ignoring the stale entry.
    totals = [sum(weights.get(name, 50) for name in shard) for shard in shards]
    assert max(totals) == 140


@pytest.mark.parametrize("count", [0, 1])
def test_service_shards_reject_empty_shards(tmp_path: Path, count: int) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service.yml").read_text())
    root = tmp_path / "packages/a13n-service/tests"
    root.mkdir(parents=True)
    for index in range(count):
        (root / f"test_{index}.py").touch()
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/service-test-durations.json").write_text("{}")
    with pytest.raises(subprocess.CalledProcessError):
        _service_matrix(workflow, "true", tmp_path, root=tmp_path)


def test_service_docs_only_changes_skip_database_tests_but_keep_checks(tmp_path: Path) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service.yml").read_text())
    jobs = workflow["jobs"]
    assert [entry["name"] for entry in _service_matrix(workflow, "false", tmp_path)] == ["checks"]
    # The tests filter is exactly the trigger list without the bundled documentation.
    classifier = next(step for step in jobs["changes"]["steps"] if step.get("id") == "filter")
    tests = yaml.safe_load(classifier["with"]["filters"])["tests"]
    for event in ("pull_request", "push"):
        assert tests == [path for path in workflow[True][event]["paths"] if path != "docs/a13n-service/**"]
    assert not any(Path("docs/a13n-service/mcp.md").full_match(pattern) for pattern in tests)


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
@pytest.mark.parametrize("changes", ["success", "failure", "cancelled", "skipped"])
def test_service_gate_requires_detection_and_every_matrix_member(changes: str, result: str) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service.yml").read_text())
    gate = workflow["jobs"]["python"]
    assert "always()" in gate["if"]
    step = gate["steps"][0]
    assert step["env"]["CHANGES_RESULT"] == "${{ needs.changes.result }}"
    assert step["env"]["VALIDATION_RESULT"] == "${{ needs.validation.result }}"
    completed = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={"CHANGES_RESULT": changes, "VALIDATION_RESULT": result},
        check=False,
    )
    assert (completed.returncode == 0) == (changes == result == "success")


@pytest.mark.parametrize(
    "path,selected",
    [
        ("e2e/service/conftest.py", True),
        ("e2e/__init__.py", True),
        ("dev/fixtures/scripted_model.py", True),
        ("scripts/tests/test_service_e2e_tooling.py", True),
        ("packages/a13n-service/a13n_service/runs/worker.py", True),
        ("packages/a13n-service/tests/test_runs.py", False),
        ("deploy/docker/images/sandbox/Dockerfile", True),
        ("crates/a13n-envd/src/main.rs", True),
        ("Cargo.toml", True),
        ("Cargo.lock", True),
        (".dockerignore", True),
        ("LICENSE", True),
        ("Makefile", True),
        ("dev/service/console_review.py", False),
        ("docs/index.mdx", False),
    ],
)
def test_service_e2e_owns_automated_scenarios(path: str, selected: bool) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service-e2e.yml").read_text())
    for event in ("pull_request", "push"):
        assert any(Path(path).full_match(pattern) for pattern in workflow[True][event]["paths"]) == selected


def test_e2e_builds_current_checkout_with_optional_layer_cache_and_loads_the_test_image() -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service-e2e.yml").read_text())
    steps = workflow["jobs"]["scenarios"]["steps"]
    build = next(step for step in steps if step.get("uses", "").startswith("docker/build-push-action@"))
    options = build["with"]
    assert options["context"] == "." and options["file"] == "deploy/docker/images/sandbox/Dockerfile"
    assert options["load"] is True and not options.get("push")
    assert options["tags"] == "a13n-sandbox:local"
    assert options["pull"] is True
    assert options["cache-from"] == "type=gha,scope=service-e2e-sandbox"
    assert options["cache-to"] == "type=gha,scope=service-e2e-sandbox,mode=max,ignore-error=true"
    # A restored cache never skips the build or test gate, and never substitutes a released daemon.
    assert "if" not in build and "continue-on-error" not in build and "build-args" not in options
    run = next(step for step in steps if step.get("run", "").startswith("make service-e2e "))
    assert steps.index(build) < steps.index(run)
    assert "--require-all" in run["run"] and "if" not in run


@pytest.mark.parametrize("support", ["success", "failure", "cancelled", "skipped"])
@pytest.mark.parametrize("scenarios", ["success", "failure", "cancelled", "skipped"])
def test_service_e2e_gate_requires_support_and_scenarios(support: str, scenarios: str) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service-e2e.yml").read_text())
    gate = workflow["jobs"]["e2e"]
    assert gate["name"] == "Service E2E" and set(gate["needs"]) == {"support", "scenarios"}
    assert "always()" in gate["if"] and "draft == false" in gate["if"]
    step = gate["steps"][0]
    assert step["env"] == {
        "SUPPORT_RESULT": "${{ needs.support.result }}",
        "SCENARIOS_RESULT": "${{ needs.scenarios.result }}",
    }
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]], env={"SUPPORT_RESULT": support, "SCENARIOS_RESULT": scenarios}, check=False
    )
    assert (result.returncode == 0) == (support == scenarios == "success")


@pytest.mark.parametrize(
    "cases,accepted",
    [("", False), ("<testcase/>", True)]
    + [(f"<testcase><{status}/></testcase>", False) for status in ("skipped", "failure", "error")],
)
def test_service_e2e_report_requires_success(tmp_path: Path, cases: str, accepted: bool) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service-e2e.yml").read_text())
    steps = workflow["jobs"]["scenarios"]["steps"]
    step = next(step for step in steps if step.get("name") == "Report collected, executed and skipped journeys")
    # Explicit Bash enables pipefail in Actions, so tee cannot hide a rejected report.
    assert step["shell"] == "bash"
    reports = tmp_path / "test-results"
    reports.mkdir()
    (reports / "service-e2e.xml").write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={**os.environ, "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")},
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) == accepted


@pytest.mark.parametrize(
    "workflow_name,job",
    [
        ("ci-a13n-envd.yml", "protocol"),
        ("ci-a13n-envd.yml", "client"),
        ("ci-a13n-envd.yml", "daemon"),
        ("ci-a13n-harness.yml", "harness"),
        ("ci-a13n-service.yml", "validation"),
        ("ci-a13n-service-e2e.yml", "scenarios"),
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
