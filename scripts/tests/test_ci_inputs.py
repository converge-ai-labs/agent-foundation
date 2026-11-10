from __future__ import annotations

import json
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
        ("scripts/tests/test_envd_ci_workflow.py", set()),
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
            ("README.md", False),
            ("LICENSE", False),
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
            if workflow_name in {"ci-a13n-service.yml", "ci-a13n-harness-ui.yml"}:
                classifier = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
                filters = yaml.safe_load(classifier["with"]["filters"])
                assert (
                    any(path.full_match(pattern) for patterns in filters.values() for pattern in patterns) == selected
                )
    for event in ("pull_request", "push"):
        for path in ("pyproject.toml", "uv.lock"):
            assert any(Path(path).full_match(pattern) for pattern in workflow[True][event]["paths"])
        if workflow_name != "ci-examples.yml":
            path = "conftest.py"
            assert any(Path(path).full_match(pattern) for pattern in workflow[True][event]["paths"])


@pytest.mark.parametrize(
    "workflow_name",
    [
        "ci-a13n-envd.yml",
        "ci-a13n-harness.yml",
        "ci-a13n-harness-ui.yml",
        "ci-a13n-service.yml",
        "ci-a13n-service-e2e.yml",
        "ci-a13n-stream-protocol.yml",
        "ci-containers.yml",
        "ci-examples.yml",
    ],
)
def test_routing_tests_do_not_select_product_ci_but_workflow_edits_do(workflow_name: str) -> None:
    workflow = yaml.safe_load((WORKFLOWS / workflow_name).read_text())
    selections = [workflow[True][event]["paths"] for event in ("pull_request", "push") if event in workflow[True]]
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if step.get("uses", "").startswith("dorny/paths-filter@"):
                selections.extend(yaml.safe_load(step["with"]["filters"]).values())
    for patterns in selections:
        for test in (
            "test_ci_inputs.py",
            "test_envd_ci_workflow.py",
            "test_harness_ui_ci_workflow.py",
            "test_image_workflows.py",
        ):
            assert not any(Path(f"scripts/tests/{test}").full_match(pattern) for pattern in patterns)
        assert any(Path(f".github/workflows/{workflow_name}").full_match(pattern) for pattern in patterns)


def test_tooling_suites_keep_one_ci_owner_and_release_manifest_inputs() -> None:
    owners = {
        "test_ci_inputs.py": "ci.yml",
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


@pytest.mark.parametrize(
    "path,selected",
    [
        ("dev/service/__main__.py", True),
        ("dev/service/tests/test_instance.py", True),
        ("dev/fixtures/model.py", True),
        ("dev/observability/state.py", True),
        ("dev/observability/langfuse.compose.yaml", True),
        ("dev/harness-ui/review.py", False),
        ("dev/harness/.env.example", False),
        ("dev/observation-demo/agent.py", False),
        ("dev/README.md", False),
    ],
)
def test_service_ci_selects_only_its_development_inputs(path: str, selected: bool) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service.yml").read_text())
    classifier = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
    filters = yaml.safe_load(classifier["with"]["filters"])
    selections = [workflow[True][event]["paths"] for event in ("pull_request", "push")]
    selections.append(filters["tests"])
    for patterns in selections:
        assert any(Path(path).full_match(pattern) for pattern in patterns) == selected


def _service_matrix(workflow: dict, tests_changed: str, tmp_path: Path, *, root: Path = ROOT) -> list[dict[str, str]]:
    step = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "matrix")
    output = tmp_path / "output"
    output.write_text("")
    subprocess.run(
        ["bash", "-c", step["run"]],
        env={**os.environ, "PYTHONPATH": str(ROOT), "TESTS_CHANGED": tests_changed, "GITHUB_OUTPUT": str(output)},
        cwd=root,
        check=True,
    )
    assert output.read_text().startswith("matrix=")
    return json.loads(output.read_text().removeprefix("matrix="))


def test_service_ci_partitions_the_current_suite_without_duplicates(tmp_path: Path) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service.yml").read_text())
    matrix = _service_matrix(workflow, "true", tmp_path)
    assert [entry["name"] for entry in matrix] == ["checks", "native", *[f"tests-{index}" for index in range(1, 11)]]
    assert all(entry["targets"] for entry in matrix[2:])
    targets = [path for entry in matrix[1:] for path in entry["targets"].splitlines()]
    assert targets
    assert len(targets) == len(set(targets))
    root = ROOT / "packages/a13n-service/tests"
    expected = {
        str(path.relative_to(ROOT))
        for path in root.rglob("*.py")
        if (path.name.startswith("test_") or path.name.endswith("_test.py"))
        and path
        not in {
            root / "test_environments_envd.py",
            root / "test_monitoring_collectors_live.py",
            root / "test_browser_native_live.py",
            root / "test_browser_selfhost_live.py",
        }
        and root / "environment_providers" not in path.parents
    }
    assert set(targets) == expected
    assert matrix == _service_matrix(workflow, "true", tmp_path)


def test_service_selection_includes_new_nested_files_and_passes_paths_literally(tmp_path: Path) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service.yml").read_text())
    root = tmp_path / "packages/a13n-service/tests"
    included = [
        "test_first.py",
        "nested/test_second.py",
        "nested/third_test.py",
        "test_space $(false).py",
        "test_fifth.py",
        "test_sixth.py",
        "test_seventh.py",
        "test_eighth.py",
        "test_ninth.py",
        "test_tenth.py",
    ]
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
    timings = tmp_path / ".github/service-test-durations.json"
    timings.parent.mkdir()
    timings.write_text(json.dumps({"packages/a13n-service/tests/deleted.py": 10000}))
    matrix = _service_matrix(workflow, "true", tmp_path, root=tmp_path)
    targets = [path for entry in matrix[1:] for path in entry["targets"].splitlines()]
    expected = sorted(str((root / name).relative_to(tmp_path)) for name in included)
    assert sorted(targets) == expected

    # Execute the actual workflow command with a fake uv to check shell argument boundaries.
    executable = tmp_path / "uv"
    executable.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    executable.chmod(0o755)
    step = next(step for step in workflow["jobs"]["validation"]["steps"] if step["name"] == "Test a13n Service")
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", "TEST_TARGETS": "\n".join(targets)},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)[-len(targets) :] == targets


@pytest.mark.parametrize("count", [0, 1, 3])
def test_service_selection_rejects_empty_shards(tmp_path: Path, count: int) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service.yml").read_text())
    root = tmp_path / "packages/a13n-service/tests"
    root.mkdir(parents=True)
    for index in range(count):
        (root / f"test_{index}.py").touch()
    timings = tmp_path / ".github/service-test-durations.json"
    timings.parent.mkdir()
    timings.write_text("{}")
    with pytest.raises(subprocess.CalledProcessError):
        _service_matrix(workflow, "true", tmp_path, root=tmp_path)


@pytest.mark.parametrize(
    "path",
    [
        "docs/a13n-service/mcp.md",
        "packages/a13n-service/README.md",
        "packages/a13n-service/LICENSE",
        "packages/a13n-logging/README.md",
        "packages/a13n-logging/LICENSE",
    ],
)
def test_service_documentation_only_changes_skip_database_tests_but_keep_checks(tmp_path: Path, path: str) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service.yml").read_text())
    classifier = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
    tests = yaml.safe_load(classifier["with"]["filters"])["tests"]
    for event in ("pull_request", "push"):
        assert any(Path(path).full_match(pattern) for pattern in workflow[True][event]["paths"])
    assert not any(Path(path).full_match(pattern) for pattern in tests)
    assert [entry["name"] for entry in _service_matrix(workflow, "false", tmp_path)] == ["checks"]


@pytest.mark.parametrize(
    "path",
    [
        ".github/service-test-durations.json",
        "scripts/run_python_tests.py",
        "packages/a13n-service/pyproject.toml",
        "packages/a13n-service/hatch_build.py",
        "packages/a13n-service/build_docs.py",
        "packages/a13n-service/.importlinter",
        "packages/a13n-service/a13n_service/runs/worker.py",
        "packages/a13n-service/tests/test_runs.py",
        "packages/a13n-logging/pyproject.toml",
        "packages/a13n-logging/a13n_logging/__init__.py",
        "packages/a13n-logging/tests/test_logging.py",
        "uv.lock",
    ],
)
def test_service_retains_runtime_tests_for_code_and_build_inputs(path: str) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service.yml").read_text())
    classifier = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
    tests = yaml.safe_load(classifier["with"]["filters"])["tests"]
    assert any(Path(path).full_match(pattern) for pattern in tests)
    for event in ("pull_request", "push"):
        assert any(Path(path).full_match(pattern) for pattern in workflow[True][event]["paths"])


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
    selection = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "paths")
    patterns = yaml.safe_load(selection["with"]["filters"])["service"]
    assert any(Path(path).full_match(pattern) for pattern in patterns) == selected
    if selected:
        for event in ("pull_request", "push"):
            assert any(Path(path).full_match(pattern) for pattern in workflow[True][event]["paths"])


@pytest.mark.parametrize(
    "path,selected",
    [
        ("packages/a13n-harness/a13n_harness/context_breakdown.py", False),
        ("frontend/apps/a13n-harness-ui/src/app.tsx", False),
        ("e2e/service/test_memory.py", False),
        ("deploy/kubernetes/values.yaml", True),
        ("deploy/docker/images/sandbox/Dockerfile", True),
        ("crates/a13n-envd/src/main.rs", True),
        ("packages/a13n-service/a13n_service/providers/environment/kubernetes.py", True),
        ("e2e/service/test_kubernetes.py", True),
    ],
)
def test_deployment_prs_select_cluster_inputs_and_main_runs_integration_changes(path, selected):
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service-e2e.yml").read_text())
    changes = workflow["jobs"]["changes"]
    paths = next(step for step in changes["steps"] if step.get("id") == "paths")
    patterns = yaml.safe_load(paths["with"]["filters"])["deploy"]
    assert any(Path(path).full_match(pattern) for pattern in patterns) == selected
    assert "github.event_name != 'pull_request'" in changes["outputs"]["deploy"]
    if selected:
        assert any(Path(path).full_match(pattern) for pattern in workflow[True]["pull_request"]["paths"])


@pytest.mark.skipif(sys.platform == "win32", reason="CI image selection runs on Ubuntu")
def test_sandbox_image_reuse_checks_source_and_pins_the_inspected_manifest(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repository, text=True).strip()

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "CI")
    (repository / "Cargo.lock").write_text("initial")
    git("add", ".")
    git("commit", "-qm", "sandbox revision")
    revision = git("rev-parse", "HEAD")
    digest = "sha256:" + "1" * 64
    image = f"ghcr.io/converge-ai-labs/a13n-sandbox@{digest}"
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/sh\ncase "$4" in\n  *:dev) echo "$INDEX" ;;\n  "$IMAGE") echo "$CONFIG" ;;\n  *) exit 1 ;;\nesac\n'
    )
    docker.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", IMAGE=image)
    env["INDEX"] = json.dumps({"manifests": [{"platform": {"os": "linux", "architecture": "amd64"}, "digest": digest}]})
    env["CONFIG"] = json.dumps(
        {"config": {"Labels": {"org.opencontainers.image.revision": revision, "ai.a13n.sandbox.variant": "standard"}}}
    )
    output = tmp_path / "output"

    def select():
        output.write_text("")
        subprocess.run(
            ["bash", ROOT / ".github/scripts/ci-sandbox-image.sh"],
            cwd=repository,
            env=dict(env, GITHUB_OUTPUT=str(output)),
            check=True,
        )
        return output.read_text().strip()

    (repository / "ui.css").write_text("body {}")
    git("add", ".")
    git("commit", "-qm", "unrelated UI change")
    assert select() == f"image={image}"
    (repository / "Cargo.lock").write_text("changed")
    git("commit", "-qam", "sandbox dependency change")
    assert select() == ""
    git("checkout", "-q", revision)
    env["CONFIG"] = "{}"
    assert select() == ""
    env["INDEX"] = "{}"
    assert select() == ""


def test_docker_journey_consumes_the_source_matched_image_or_successful_same_run_build():
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service-e2e.yml").read_text())
    jobs = workflow["jobs"]
    assert jobs["scenarios"]["needs"] == "changes"
    assert jobs["sandbox-image"]["needs"] == "sandbox-binary"
    assert "needs.changes.outputs.image == ''" in jobs["sandbox-binary"]["if"]
    for name in ("docker", "deployment"):
        assert jobs[name]["needs"] == ["changes", "sandbox-image"]
        assert "needs.changes.outputs.image != '' || needs.sandbox-image.result == 'success'" in jobs[name]["if"]
    steps = jobs["docker"]["steps"]
    load = next(step for step in steps if step.get("uses") == "./.github/actions/load-ci-sandbox")
    assert load["with"]["tag"] == "a13n-sandbox:local"
    run = next(step for step in steps if step.get("run", "").startswith("make service-e2e "))
    assert steps.index(load) < steps.index(run)
    assert "--require-all" in run["run"] and "if" not in run
    assert "-k docker" in run["run"]
    assert workflow["jobs"]["scenarios"]["strategy"]["fail-fast"] is False
    reports = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert reports["with"]["name"] == "service-e2e-docker-${{ github.run_attempt }}"


@pytest.mark.parametrize(
    "support,scenarios,docker",
    [("success", "success", "success")]
    + [
        tuple(status if index == failed else "success" for index in range(3))
        for failed in range(3)
        for status in ("failure", "cancelled", "skipped")
    ],
)
def test_service_e2e_gate_requires_support_and_scenarios(support: str, scenarios: str, docker: str) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-service-e2e.yml").read_text())
    gate = workflow["jobs"]["e2e"]
    assert gate["name"] == "Service E2E" and set(gate["needs"]) == {"changes", "support", "scenarios", "docker"}
    assert "always()" in gate["if"] and "needs.changes.result == 'failure'" in gate["if"]
    step = gate["steps"][0]
    assert step["env"] == {
        "SUPPORT_RESULT": "${{ needs.support.result }}",
        "SCENARIOS_RESULT": "${{ needs.scenarios.result }}",
        "DOCKER_RESULT": "${{ needs.docker.result }}",
    }
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={"SUPPORT_RESULT": support, "SCENARIOS_RESULT": scenarios, "DOCKER_RESULT": docker},
        check=False,
    )
    assert (result.returncode == 0) == (support == scenarios == docker == "success")


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


@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped"])
def test_harness_gate_requires_every_matrix_member(result: str) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-harness.yml").read_text())
    gate = workflow["jobs"]["linux"]
    assert gate["name"] == "a13n Harness (Linux)" and gate["needs"] == "harness"
    assert "always()" in gate["if"]
    step = gate["steps"][0]
    assert step["env"] == {"RESULT": "${{ needs.harness.result }}"}
    completed = subprocess.run(["bash", "-e", "-c", step["run"]], env={"RESULT": result}, check=False)
    assert (completed.returncode == 0) == (result == "success")


@pytest.mark.parametrize(
    "chart,helm,journeys",
    [("success", "success", "success")]
    + [
        tuple(status if index == failed else "success" for index in range(3))
        for failed in range(3)
        for status in ("failure", "cancelled", "skipped")
    ],
)
def test_deployment_gate_requires_chart_helm_and_every_journey_shard(chart: str, helm: str, journeys: str) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-deploy.yml").read_text())
    gate = workflow["jobs"]["kind"]
    assert gate["name"] == "Helm install and upgrade on kind"
    assert set(gate["needs"]) == {"chart", "helm", "journeys"} and "always()" in gate["if"]
    step = gate["steps"][0]
    assert step["env"] == {
        "CHART_RESULT": "${{ needs.chart.result }}",
        "HELM_RESULT": "${{ needs.helm.result }}",
        "JOURNEYS_RESULT": "${{ needs.journeys.result }}",
    }
    completed = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={"CHART_RESULT": chart, "HELM_RESULT": helm, "JOURNEYS_RESULT": journeys},
        check=False,
    )
    assert (completed.returncode == 0) == (chart == helm == journeys == "success")


def test_cluster_jobs_load_the_same_sandbox_before_startup():
    jobs = yaml.safe_load((WORKFLOWS / "ci-deploy.yml").read_text())["jobs"]
    for name in ("helm", "journeys"):
        steps = jobs[name]["steps"]
        load = next(step for step in steps if step.get("uses") == "./.github/actions/load-ci-sandbox")
        assert load["with"] == {"image": "${{ inputs.image }}", "tag": "a13n-sandbox:ci"}
        assert steps.index(load) < next(
            i for i, step in enumerate(steps) if step["name"] == "Require successful cluster creation"
        )
    assert jobs["helm"]["env"]["K8S_SANDBOX_IMAGE"] == "a13n-sandbox:ci"


def test_harness_keeps_full_discovery_and_native_browser_coverage() -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-harness.yml").read_text())
    job = workflow["jobs"]["harness"]
    matrix = job["strategy"]["matrix"]["include"]
    assert job["strategy"]["fail-fast"] is False
    assert [entry["shard"] for entry in matrix if "shard" in entry] == [1, 2]
    steps = job["steps"]
    unit = next(step for step in steps if step.get("name") == "Test a13n Harness shard")
    assert unit["if"] == "startsWith(matrix.name, 'tests-')"
    assert "scripts/run_python_tests.py" in unit["run"]
    assert "--shard ${{ matrix.shard }} 2 packages/a13n-harness/tests" in unit["run"]
    browser = next(step for step in steps if step.get("name") == "Test native Chromium browser workflows")
    assert browser["if"] == "matrix.name == 'browser'"
    assert "test_browser_cdp_native.py" in browser["run"] and "test_browser_workflows_native.py" in browser["run"]
    assert "--browser-executable=" in browser["run"]
    assert any(entry["name"] == "Windows" for entry in matrix)


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


@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped"])
def test_windows_ui_gate_requires_all_full_suite_shards(result):
    workflow = yaml.safe_load((WORKFLOWS / "ci-a13n-harness-ui.yml").read_text())
    windows = workflow["jobs"]["windows"]
    assert windows["strategy"]["fail-fast"] is False
    assert "'[1]' || '[1,2,3,4]'" in windows["strategy"]["matrix"]["shard"]
    full = next(step for step in windows["steps"] if step["name"] == "Test full UI suite on Windows")
    assert "--shard ${{ matrix.shard }} 4" in full["run"]
    assert "packages/a13n-harness-ui/tests" in full["run"]
    assert "--workers 4" in full["run"]
    native = next(step for step in windows["steps"] if step["name"] == "Test native command lifecycle")
    assert native["if"] == "matrix.shard == 1"
    gate = workflow["jobs"]["windows-result"]
    assert gate["name"] == "UI (Windows)" and "always()" in gate["if"]
    assert gate["needs"] == ["changes", "windows"]
    completed = subprocess.run(
        ["bash", "-e", "-c", gate["steps"][0]["run"]], env={"WINDOWS_RESULT": result}, check=False
    )
    assert (completed.returncode == 0) == (result == "success")
