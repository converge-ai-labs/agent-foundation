from __future__ import annotations

import json
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
    for name in ("A13N_SERVICE_CHANGED", "SANDBOX_CHANGED", "HARNESS_UI_CHANGED", "DOCKER_ENVIRONMENT_CHANGED"):
        monkeypatch.setenv(name, "false")
    matrix = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "matrix")
    subprocess.run(["bash", "-eu", "-c", matrix["run"]], check=True)
    images = json.loads(output.read_text().removeprefix("images="))
    assert {image["name"] for image in images} == {
        "a13n-service",
        "a13n-sandbox",
        "a13n-harness-ui",
        "a13n-docker-environment",
    }
    assert (
        next(image for image in images if image["name"] == "a13n-harness-ui")["context"] == "dist/a13n-harness-ui-image"
    )
    assert {image["name"]: image["platforms"] for image in images} == {
        "a13n-service": "linux/amd64,linux/arm64",
        "a13n-docker-environment": "linux/amd64,linux/arm64",
        "a13n-sandbox": "linux/amd64",
        "a13n-harness-ui": "linux/amd64",
    }


@pytest.mark.parametrize("component", ["a13n-service", "a13n-envd", "a13n-harness-ui"])
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


def test_ui_release_reuses_published_wheel_without_rebuilding_browser() -> None:
    jobs = yaml.safe_load((ROOT / ".github/workflows/release-a13n-harness-ui.yml").read_text())["jobs"]
    steps = jobs["publish-image"]["steps"]
    artifact = next(step for step in steps if step.get("uses", "").startswith("actions/download-artifact"))
    assert artifact["with"]["name"] == "a13n-harness-ui-python-dist"
    commands = "\n".join(step.get("run", "") for step in steps)
    assert "prepare_harness_ui_image.py --release-dist dist/release" in commands
    assert "pnpm" not in str(steps) and "pytest" not in commands


@pytest.mark.parametrize("component", ["a13n-service", "a13n-harness-ui"])
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
            # Envd prepares once, then all native/image jobs apply the same patch.
            prepare_steps = jobs["prepare"]["steps"]
            assert any(
                'prepare-release-version.py a13n-envd "$version"' in step.get("run", "") for step in prepare_steps
            )
            patch = next(step for step in prepare_steps if step["name"] == "Create release version patch")
            assert "Cargo.toml" in patch["run"] and "Cargo.lock" in patch["run"]
            preparation = next(index for index, step in enumerate(steps) if "git apply" in step.get("run", ""))
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
        ("packages/a13n-harness/a13n_harness/types.py", {"service", "harness_ui"}),
        ("packages/a13n-envd-client/a13n_envd_client/eip/client.py", {"service", "harness_ui"}),
        ("packages/a13n-logging/a13n_logging/__init__.py", {"service", "harness_ui"}),
        ("packages/a13n-harness/pyproject.toml", {"service", "harness_ui"}),
        ("packages/a13n-harness/LICENSE", {"service", "harness_ui"}),
        ("packages/a13n-harness-ui/pyproject.toml", {"service", "harness_ui"}),
        ("packages/a13n-harness-ui/hatch_build.py", {"harness_ui"}),
        ("packages/a13n-harness-ui/build_skills.py", {"harness_ui"}),
        ("packages/a13n-harness-ui/a13n_harness_ui/app.py", {"harness_ui"}),
        ("frontend/apps/a13n-harness-ui/src/shell/workbench.tsx", {"harness_ui"}),
        ("frontend/packages/a13n-ui/src/components/button.tsx", {"service", "harness_ui"}),
        ("frontend/pnpm-lock.yaml", {"service", "harness_ui"}),
        ("frontend/tsconfig.base.json", {"service", "harness_ui"}),
        ("docs/a13n-harness-ui/configuration.md", {"harness_ui"}),
        ("mkdocs.yml", {"harness_ui"}),
        ("deploy/docker/images/a13n-service/service.toml", {"service"}),
        ("deploy/docker/images/a13n-service/entrypoint.sh", {"service"}),
        ("deploy/docker/images/sandbox/Dockerfile", {"sandbox"}),
        ("crates/a13n-envd/src/main.rs", {"sandbox"}),
        ("uv.lock", {"service", "harness_ui"}),
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
        "${A13N_SERVICE_IMAGE:-a13n-service:local}",
        "${A13N_SERVICE_IMAGE:-ghcr.io/converge-ai-labs/a13n-service:1.2.3-rc.1}",
    )
    assert pinned != source
    assert [path.name for path in (tmp_path / "dist").iterdir()] == ["a13n-service.yaml"]
