from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]


def test_development_images_publish_only_dev_without_smoke_or_sha_tags(tmp_path, monkeypatch) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/images.yml").read_text())
    assert workflow[True]["push"]["branches"] == ["main"]
    # Incremental matrices must not cancel or drop another component's publication.
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert workflow["concurrency"]["queue"] == "max"
    assert workflow["jobs"]["changes"]["if"] == "github.ref == 'refs/heads/main'"
    steps = workflow["jobs"]["publish"]["steps"]
    metadata = next(step for step in steps if step.get("id") == "meta")
    assert metadata["with"]["tags"].strip() == "type=raw,value=dev"
    build = next(step for step in steps if step.get("uses", "").startswith("docker/build-push-action"))
    assert build["with"]["push"] is True
    assert "BUILD_REVISION=${{ github.sha }}" in build["with"]["build-args"]
    assert "Smoke-check" not in str(steps)
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    for name in ("A13N_SERVICE_CHANGED", "SANDBOX_CHANGED"):
        monkeypatch.setenv(name, "false")
    matrix = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "matrix")
    subprocess.run(["bash", "-eu", "-c", matrix["run"]], check=True)
    images = json.loads(output.read_text().removeprefix("images="))
    assert {image["name"] for image in images} == {
        "a13n-service",
        "a13n-sandbox",
    }
    assert {image["name"]: image["platforms"] for image in images} == {
        "a13n-service": "linux/amd64,linux/arm64",
        "a13n-sandbox": "linux/amd64,linux/arm64",
    }


@pytest.mark.parametrize("component", ["a13n-service", "a13n-envd"])
@pytest.mark.parametrize("version", ["1.2.3", "1.2.3-rc.2"])
def test_all_release_image_tags_follow_one_channel_policy(component, version, tmp_path, monkeypatch) -> None:
    jobs = yaml.safe_load((ROOT / f".github/workflows/release-{component}.yml").read_text())["jobs"]
    steps = jobs["publish-image"]["steps"]
    script = next(step["run"] for step in steps if step["name"] == "Resolve image tags")
    output = tmp_path / "output"
    image = f"ghcr.io/example/{component}"
    monkeypatch.setenv("IMAGE", image)
    monkeypatch.setenv("VERSION", version)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    subprocess.run(["bash", "-eu", "-c", script], check=True)
    tags = [line for line in output.read_text().splitlines() if line.startswith(image)]
    assert tags == ([f"{image}:{version}"] if "-rc." in version else [f"{image}:{version}", f"{image}:latest"])


@pytest.mark.parametrize("component", ["a13n-service"])
@pytest.mark.parametrize(
    "tag,installed,accepted",
    [
        ("1.2.3", "1.2.3", True),
        ("1.2.3-rc.2", "1.2.3rc2", True),
        ("1.2.3", "0.0.0", False),
        ("1.2.3-rc.2", "1.2.3rc1", False),
        ("dev", "0.0.0", True),
    ],
)
def test_python_image_build_checks_installed_release_identity(component, tag, installed, accepted, monkeypatch) -> None:
    import shlex
    import sys

    dockerfile = (ROOT / f"deploy/docker/images/{component}/Dockerfile").read_text()
    command = next(line.removeprefix("RUN ") for line in dockerfile.splitlines() if line.startswith("RUN python -c "))
    program = shlex.split(command)[2]
    monkeypatch.setenv("BUILD_VERSION", tag)
    # Execute the actual Docker build assertion with controlled distribution metadata.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import importlib.metadata; importlib.metadata.version = lambda name: {installed!r}; exec({program!r})",
        ],
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is accepted, result.stderr


def test_release_images_prepare_real_metadata_before_building() -> None:
    for component in ("a13n-service", "a13n-envd"):
        jobs = yaml.safe_load((ROOT / f".github/workflows/release-{component}.yml").read_text())["jobs"]
        steps = jobs["publish-image"]["steps"]
        if component == "a13n-envd":
            # Envd prepares once; the image reuses the versioned native artifacts.
            prepare_steps = jobs["prepare"]["steps"]
            assert any(
                'prepare-release-version.py a13n-envd "$version"' in step.get("run", "") for step in prepare_steps
            )
            patch = next(step for step in prepare_steps if step["name"] == "Create release version patch")
            assert "Cargo.toml" in patch["run"] and "Cargo.lock" in patch["run"]
            preparation = next(index for index, step in enumerate(steps) if step["name"] == "Stage sandbox binaries")
        else:
            preparation = next(
                index for index, step in enumerate(steps) if "prepare-release-version.py" in step.get("run", "")
            )
            assert f'prepare-release-version.py {component} "$VERSION"' in steps[preparation]["run"]
        build = next(
            index for index, step in enumerate(steps) if step.get("uses", "").startswith("docker/build-push-action")
        )
        assert preparation < build
    sandbox = (ROOT / "deploy/docker/images/sandbox/Dockerfile").read_text()
    assert 'test "$(a13n-envd --version)" = "a13n-envd $BUILD_VERSION"' in sandbox


def test_sandbox_shares_trixie_runtime_with_source_and_prebuilt_binaries() -> None:
    dockerfile = (ROOT / "deploy/docker/images/sandbox/Dockerfile").read_text()
    assert "ARG ENVD_BINARY_SOURCE=source" in dockerfile
    assert "FROM rust:1.96-trixie AS builder" in dockerfile
    assert "FROM node:24-trixie-slim AS node" in dockerfile
    assert "FROM python:3.13.12-slim-trixie AS runtime" in dockerfile
    assert "cargo build --release --locked --package a13n-envd" in dockerfile
    assert "COPY --from=builder /src/target/release/a13n-envd /a13n-envd" in dockerfile
    assert "COPY --chmod=0755 tmp/sandbox-binaries/${TARGETARCH}/a13n-envd /a13n-envd" in dockerfile
    assert "FROM envd-${ENVD_BINARY_SOURCE} AS envd" in dockerfile
    assert "COPY --from=envd /a13n-envd /usr/local/bin/a13n-envd" in dockerfile
    assert "tmp" not in (ROOT / ".dockerignore").read_text().splitlines()
    steps = yaml.safe_load((ROOT / ".github/workflows/images.yml").read_text())["jobs"]["publish"]["steps"]
    build = next(step for step in steps if step.get("uses", "").startswith("docker/build-push-action"))
    assert "ENVD_BINARY_SOURCE=prebuilt" not in build["with"]["build-args"]


@pytest.mark.parametrize(
    "package",
    [
        "a13n-envd-client",
        "a13n-harness",
        "a13n-stream-protocol",
        "a13n-harness-ui",
        "a13n-logging",
        "a13n-service",
    ],
)
@pytest.mark.parametrize("suffix", ["tests/test_example.py", "tests/conftest.py", "tests/fixtures/data.json"])
@pytest.mark.parametrize("workflow_name,event", [("images.yml", "push"), ("ci-containers.yml", "pull_request")])
def test_package_tests_do_not_trigger_images(package: str, suffix: str, workflow_name: str, event: str) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows" / workflow_name).read_text())
    path = Path(f"packages/{package}/{suffix}")
    assert not any(path.full_match(pattern) for pattern in workflow[True][event]["paths"])
    step = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
    filters = yaml.safe_load(step["with"]["filters"])
    assert not any(path.full_match(pattern) for patterns in filters.values() for pattern in patterns)


@pytest.mark.parametrize(
    "path,expected",
    [
        ("packages/a13n-service/a13n_service/app.py", {"service"}),
        ("packages/a13n-service/a13n_service/migrations/versions/initial.py", {"service"}),
        ("packages/a13n-service/README.md", {"service"}),
        ("packages/a13n-harness/a13n_harness/types.py", {"service"}),
        ("packages/a13n-envd-client/a13n_envd_client/eip/client.py", {"service"}),
        ("packages/a13n-logging/a13n_logging/__init__.py", {"service"}),
        ("packages/a13n-harness/pyproject.toml", {"service"}),
        ("packages/a13n-harness/LICENSE", {"service"}),
        ("packages/a13n-harness-ui/pyproject.toml", {"service"}),
        ("packages/a13n-harness-ui/hatch_build.py", set()),
        ("packages/a13n-harness-ui/build_skills.py", set()),
        ("packages/a13n-harness-ui/a13n_harness_ui/app.py", set()),
        ("frontend/apps/a13n-harness-ui/src/shell/workbench.tsx", set()),
        ("frontend/packages/a13n-ui/src/components/button.tsx", {"service"}),
        ("frontend/pnpm-lock.yaml", {"service"}),
        ("frontend/tsconfig.base.json", {"service"}),
        ("docs/a13n-harness-ui/configuration.md", set()),
        ("docs/a13n-harness-ui/meta.json", set()),
        ("deploy/docker/images/a13n-service/service.toml", {"service"}),
        ("deploy/docker/images/a13n-service/entrypoint.sh", {"service"}),
        ("deploy/docker/images/sandbox/Dockerfile", {"sandbox"}),
        ("crates/a13n-envd/src/main.rs", {"sandbox"}),
        ("uv.lock", {"service"}),
        ("frontend/apps/a13n-console/src/app.tsx", {"service"}),
        ("proto/a13n-service/openapi.json", {"service"}),
        ("frontend/apps/a13n-docs/index.md", set()),
    ],
)
def test_development_images_retain_real_build_inputs(path: str, expected: set[str]) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/images.yml").read_text())
    step = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
    filters = yaml.safe_load(step["with"]["filters"])
    assert all(not pattern.startswith("!") for patterns in filters.values() for pattern in patterns)
    actual = {name for name, patterns in filters.items() if any(Path(path).full_match(pattern) for pattern in patterns)}
    assert actual == expected
    assert any(Path(path).full_match(pattern) for pattern in workflow[True]["push"]["paths"]) == bool(expected)


@pytest.mark.parametrize(
    "name",
    [
        "deploy/docker/images/a13n-service/service.toml",
        "deploy/docker/images/a13n-service/entrypoint.sh",
        "deploy/docker/compose/a13n-service.yaml",
        "scripts/deploy_smoke.py",
    ],
)
def test_container_checks_retain_service_runtime_configuration(name: str) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci-containers.yml").read_text())
    path = Path(name)
    assert any(path.full_match(pattern) for pattern in workflow[True]["pull_request"]["paths"])
    step = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
    filters = yaml.safe_load(step["with"]["filters"])
    assert any(path.full_match(pattern) for pattern in filters["service"])


def test_service_release_publishes_both_architectures_and_pins_its_compose_image(tmp_path, monkeypatch) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release-a13n-service.yml").read_text())
    jobs = workflow["jobs"]
    build = next(s for s in jobs["publish-image"]["steps"] if s.get("uses", "").startswith("docker/build-push-action"))
    assert build["with"]["platforms"] == "linux/amd64,linux/arm64"
    assert "publish-chart" in jobs["create-release"]["needs"]
    compose = ROOT / "deploy/docker/compose"
    (tmp_path / "deploy").mkdir()
    shutil.copytree(compose, tmp_path / "deploy/docker/compose")
    (tmp_path / "dist").mkdir()
    step = next(s for s in jobs["create-release"]["steps"] if s["name"].startswith("Pin the release image"))
    monkeypatch.setenv("A13N_SERVICE_IMAGE", workflow["env"]["A13N_SERVICE_IMAGE"])
    monkeypatch.setenv("VERSION", "1.2.3-rc.1")
    subprocess.run(["bash", "-eu", "-c", step["run"]], check=True, cwd=tmp_path)
    source = (compose / "a13n-service.yaml").read_text()
    pinned = (tmp_path / "dist/a13n-service.yaml").read_text()
    assert pinned == source.replace(
        "${A13N_SERVICE_IMAGE:-ghcr.io/converge-ai-labs/a13n-service:latest}",
        "${A13N_SERVICE_IMAGE:-ghcr.io/converge-ai-labs/a13n-service:1.2.3-rc.1}",
    )
    assert pinned != source
    quickstart = (compose / "a13n-service-quickstart.yaml").read_text()
    assert (tmp_path / "dist/a13n-service-quickstart.yaml").read_text() == quickstart.replace(
        "${A13N_SERVICE_IMAGE:-ghcr.io/converge-ai-labs/a13n-service:dev}",
        "${A13N_SERVICE_IMAGE:-ghcr.io/converge-ai-labs/a13n-service:1.2.3-rc.1}",
    )
    assert sorted(path.name for path in (tmp_path / "dist").iterdir()) == [
        "a13n-service-quickstart.yaml",
        "a13n-service.yaml",
    ]


@pytest.mark.parametrize("available", [True, False])
def test_service_release_requires_its_reviewed_sandbox(available, tmp_path, monkeypatch) -> None:
    jobs = yaml.safe_load((ROOT / ".github/workflows/release-a13n-service.yml").read_text())["jobs"]
    steps = jobs["build-python"]["steps"]
    gate = next(step for step in steps if step["name"] == "Verify reviewed sandbox release is published")
    assert steps.index(gate) < next(i for i, step in enumerate(steps) if step.get("id") == "version")
    assert jobs["publish-python"]["needs"] == "build-python"
    output = tmp_path / "image"
    docker = tmp_path / "docker"
    docker.write_text(f'#!/bin/sh\necho "$*" > "{output}"\nexit {0 if available else 1}\n')
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    result = subprocess.run(["bash", "-eu", "-c", gate["run"]], cwd=ROOT, capture_output=True, text=True)
    assert (result.returncode == 0) is available, result.stderr
    assert output.read_text().strip() == "manifest inspect ghcr.io/converge-ai-labs/a13n-sandbox:0.1.3"


def test_sandbox_image_changes_trigger_native_docker_e2e() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci-a13n-service-e2e.yml").read_text())
    image = Path("deploy/docker/images/sandbox/Dockerfile")
    for event in ("pull_request", "push"):
        assert any(image.full_match(pattern) for pattern in workflow[True][event]["paths"])


def test_only_service_and_sandbox_images_are_published() -> None:
    service = yaml.safe_load((ROOT / ".github/workflows/release-a13n-service.yml").read_text())["jobs"]
    ui = yaml.safe_load((ROOT / ".github/workflows/release-a13n-harness-ui.yml").read_text())["jobs"]
    sandbox = yaml.safe_load((ROOT / ".github/workflows/release-a13n-envd.yml").read_text())["jobs"]
    assert "publish-environment-image" not in service
    assert service["publish-python"]["needs"] == "build-python"
    assert "publish-image" not in ui
    assert set(ui["create-release"]["needs"]) == {"build-python", "publish-python"}
    build = next(
        step
        for step in sandbox["publish-image"]["steps"]
        if step.get("uses", "").startswith("docker/build-push-action")
    )
    assert build["with"]["platforms"] == "linux/amd64,linux/arm64"
    assert build["with"]["file"] == "deploy/docker/images/sandbox/Dockerfile"


def test_quickstart_waits_for_initialization_without_host_authority() -> None:
    document = yaml.safe_load((ROOT / "deploy/docker/compose/a13n-service-quickstart.yaml").read_text())
    services = document["services"]
    assert document["name"] != "a13n-service"
    assert services["service"]["depends_on"]["init"]["condition"] == "service_completed_successfully"
    assert services["init"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["service"]["environment"]["A13N_DATABASE__AUTO_MIGRATE"] == "false"
    assert services["service"]["ports"] == ["127.0.0.1:${A13N_PORT:-8080}:8000"]
    assert services["service"]["volumes"] == ["service-data:/app/var"]
    assert services["init"]["healthcheck"] == {"disable": True}
    for name, service in services.items():
        assert "user" not in service
        if name != "service":
            assert "ports" not in service


@pytest.mark.parametrize(
    ("migration_status", "bootstrap_status", "expected"), [(0, 0, 0), (0, 3, 0), (0, 1, 1), (0, 2, 2), (1, 0, 1)]
)
def test_quickstart_initializer_propagates_failures(
    tmp_path, monkeypatch, migration_status, bootstrap_status, expected
):
    document = yaml.safe_load((ROOT / "deploy/docker/compose/a13n-service-quickstart.yaml").read_text())
    command = document["services"]["init"]["command"]
    calls = tmp_path / "calls"
    executable = tmp_path / "a13n-service"
    executable.write_text(
        "#!/bin/sh\n"
        f'echo "$3" >> "{calls}"\n'
        f'if [ "$3" = migrate ]; then exit {migration_status}; fi\n'
        "read -r password\n"
        '[ "$password" = local-public-password-123 ] || exit 99\n'
        f"exit {bootstrap_status}\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    result = subprocess.run([*command[:2], command[2].replace("$$", "$")], check=False)
    assert result.returncode == expected
    assert calls.read_text().splitlines() == (["migrate"] if migration_status else ["migrate", "bootstrap"])
