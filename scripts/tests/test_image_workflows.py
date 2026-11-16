from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("legacy_image", [None, "", "a13n-sandbox:ignored"])
@pytest.mark.parametrize("pull_policy", [None, "if_missing", "never"])
def test_compose_renders_default_and_complete_docker_overrides(tmp_path, legacy_image, pull_policy) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is not installed")
    version = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, check=False)
    if version.returncode:
        pytest.skip("Docker Compose is not installed")
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("A13N_", "COMPOSE_"))}
    if legacy_image is not None:
        environment["A13N_DOCKER_ENVIRONMENT_IMAGE"] = legacy_image
    env_file = tmp_path / ".env"
    env_file.write_text("")
    command = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(ROOT / "deploy/docker/compose/a13n-service.yaml"),
    ]
    expected = {"enabled": True}
    if pull_policy is not None:
        expected = {"enabled": True, "image": "a13n-sandbox:local", "pull_policy": pull_policy}
        override = tmp_path / "compose.local.yaml"
        override.write_text(
            yaml.safe_dump(
                {
                    "services": {"service": {"environment": {"A13N_PROVISIONING__DOCKER": json.dumps(expected)}}},
                }
            )
        )
        command.extend(["-f", str(override)])
    result = subprocess.run(
        [*command, "config", "--format", "json"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    rendered = json.loads(result.stdout)["services"]["service"]["environment"]["A13N_PROVISIONING__DOCKER"]
    assert json.loads(rendered) == expected


@pytest.mark.parametrize(
    "event,service_changed,browser_changed,sandbox_changed,expected",
    [
        ("workflow_dispatch", "false", "false", "false", {"a13n-service", "a13n-browser", "a13n-sandbox"}),
        ("push", "true", "false", "false", {"a13n-service"}),
        ("push", "false", "true", "false", {"a13n-browser"}),
        ("push", "false", "false", "true", {"a13n-sandbox"}),
        ("push", "true", "true", "true", {"a13n-service", "a13n-browser", "a13n-sandbox"}),
        ("push", "false", "false", "false", set()),
    ],
)
def test_development_images_publish_only_variant_dev_tags_without_smoke_or_sha_tags(
    tmp_path, monkeypatch, event, service_changed, browser_changed, sandbox_changed, expected
) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/images.yml").read_text())
    assert workflow[True]["push"]["branches"] == ["main"]
    # Incremental matrices must not cancel or drop another component's publication.
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert workflow["concurrency"]["queue"] == "max"
    assert workflow["jobs"]["changes"]["if"] == "github.ref == 'refs/heads/main'"
    steps = workflow["jobs"]["publish"]["steps"]
    metadata = next(step for step in steps if step.get("id") == "meta")
    assert metadata["with"]["tags"].strip() == "type=raw,value=${{ matrix.tag }}"
    build = next(step for step in steps if step.get("uses", "").startswith("docker/build-push-action"))
    assert build["with"]["push"] is True
    assert "BUILD_REVISION=${{ github.sha }}" in build["with"]["build-args"]
    assert "Smoke-check" not in str(steps)
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    monkeypatch.setenv("A13N_SERVICE_CHANGED", service_changed)
    monkeypatch.setenv("BROWSER_CHANGED", browser_changed)
    monkeypatch.setenv("SANDBOX_CHANGED", sandbox_changed)
    matrix = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "matrix")
    subprocess.run(["bash", "-eu", "-c", matrix["run"]], check=True)
    images = json.loads(output.read_text().removeprefix("images="))
    assert {image["name"] for image in images} == expected
    assert all(image["platforms"] == "linux/amd64,linux/arm64" for image in images)
    sandbox = [image for image in images if image["name"] == "a13n-sandbox"]
    assert {(image["variant"], image["tag"]) for image in sandbox} == (
        {
            ("standard", "dev"),
            ("slim", "dev-slim"),
            ("desktop", "dev-desktop"),
            ("standard", "dev-vm"),
            ("slim", "dev-slim-vm"),
            ("desktop", "dev-desktop-vm"),
        }
        if "a13n-sandbox" in expected
        else set()
    )
    assert all(image["tag"] == "dev" for image in images if image["name"] in {"a13n-service", "a13n-browser"})
    assert "SANDBOX_VARIANT={0}" in build["with"]["build-args"]
    assert "matrix.tag" in build["with"]["cache-to"]


@pytest.mark.parametrize("result", ["success", "failure", "skipped", "cancelled"])
def test_development_sandbox_publication_requires_both_binary_builds(result, monkeypatch) -> None:
    steps = yaml.safe_load((ROOT / ".github/workflows/images.yml").read_text())["jobs"]["publish"]["steps"]
    gate = next(step for step in steps if step["name"] == "Require successful sandbox binaries")
    monkeypatch.setenv("BINARY_BUILD_RESULT", result)
    completed = subprocess.run(["bash", "-eu", "-c", gate["run"]], check=False)
    assert (completed.returncode == 0) is (result == "success")


@pytest.mark.parametrize(
    "component,variant",
    [
        ("a13n-service", "standard"),
        ("a13n-service", "browser"),
        ("a13n-envd", "standard"),
        ("a13n-envd", "slim"),
        ("a13n-envd", "desktop"),
    ],
)
@pytest.mark.parametrize("envd_variant", ["default", "vm"])
@pytest.mark.parametrize("version", ["1.2.3", "1.2.3-rc.2"])
def test_all_release_image_tags_follow_one_channel_policy(
    component, variant, envd_variant, version, tmp_path, monkeypatch
) -> None:
    jobs = yaml.safe_load((ROOT / f".github/workflows/release-{component}.yml").read_text())["jobs"]
    steps = jobs["publish-image"]["steps"]
    script = next(step["run"] for step in steps if step["name"] == "Resolve image tags")
    output = tmp_path / "output"
    image_name = "a13n-browser" if variant == "browser" else component
    image = f"ghcr.io/example/{image_name}"
    monkeypatch.setenv("IMAGE_NAME", image_name)
    monkeypatch.setenv("IMAGE", image)
    monkeypatch.setenv("VERSION", version)

    monkeypatch.setenv("VARIANT", variant)
    monkeypatch.setenv("ENVD_VARIANT", "default")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("ENVD_VARIANT", envd_variant)
    subprocess.run(["bash", "-eu", "-c", script], check=True)
    tags = [line for line in output.read_text().splitlines() if line.startswith(image)]
    suffix = f"-{variant}" if variant not in {"standard", "browser"} else ""
    stable = variant if variant != "standard" else "latest"
    if component == "a13n-envd" and envd_variant == "vm":
        suffix += "-vm"
        stable = "vm" if variant == "standard" else f"{variant}-vm"
    expected = [f"{image}:{version}{suffix}"]
    if "-rc." not in version and variant != "browser":
        expected.append(f"{image}:{stable}")
    assert tags == expected


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
        ("deploy/docker/images/sandbox/entrypoint.sh", {"sandbox"}),
        ("deploy/docker/images/sandbox/desktop.py", {"sandbox"}),
        (
            "packages/a13n-service/a13n_service/providers/environment/desktop_guest.py",
            {"service", "browser", "sandbox"},
        ),
        ("crates/a13n-envd/src/daemon_supervisor.rs", {"sandbox"}),
        ("deploy/docker/images/sandbox/init-skills.sh", {"sandbox"}),
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
    browser = (compose / "a13n-service-browser.yaml").read_text()
    assert (tmp_path / "dist/a13n-service-browser.yaml").read_text() == browser.replace(
        "${A13N_SERVICE_IMAGE:-ghcr.io/converge-ai-labs/a13n-service:latest}",
        "${A13N_SERVICE_IMAGE:-ghcr.io/converge-ai-labs/a13n-service:1.2.3-rc.1}",
    )
    assert sorted(path.name for path in (tmp_path / "dist").iterdir()) == [
        "a13n-service-browser.yaml",
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
    assert output.read_text().strip() == "manifest inspect ghcr.io/converge-ai-labs/a13n-sandbox:0.8.2"


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


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3-rc.1"])
@pytest.mark.parametrize("variant", ["standard", "slim", "desktop"])
def test_sandbox_release_checks_each_variant_digest(version, variant, tmp_path, monkeypatch):
    jobs = yaml.safe_load((ROOT / ".github/workflows/release-a13n-envd.yml").read_text())["jobs"]
    publish = jobs["publish-image"]
    assert {(row["variant"], row["envd-variant"]) for row in publish["strategy"]["matrix"]["include"]} == {
        ("standard", "slim"),
        ("slim", "slim"),
        ("desktop", "default"),
        ("standard", "vm"),
        ("slim", "vm"),
        ("desktop", "vm"),
    }
    steps = publish["steps"]
    build = next(step for step in steps if step.get("uses", "").startswith("docker/build-push-action"))
    assert "SANDBOX_VARIANT=${{ matrix.variant }}" in build["with"]["build-args"]
    assert "matrix.variant" in build["with"]["cache-to"]
    step = next(step for step in steps if step["name"] == "Verify published image tags")
    fake_docker = tmp_path / "docker"
    fake_docker.write_text('#!/bin/sh\necho "$4" >> images\necho sha256:same\n')
    fake_docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("IMAGE", "example/sandbox")
    monkeypatch.setenv("VERSION", version)
    monkeypatch.setenv("VARIANT", variant)
    monkeypatch.setenv("ENVD_VARIANT", "default")
    subprocess.run(["bash", "-eu", "-c", step["run"]], cwd=tmp_path, check=True)
    suffix = f"-{variant}" if variant != "standard" else ""
    expected = [f"example/sandbox:{version}{suffix}"]
    if "-rc." not in version:
        expected.append(f"example/sandbox:{variant if variant != 'standard' else 'latest'}")
    assert (tmp_path / "images").read_text().splitlines() == expected


def test_container_ci_checks_all_variants_on_both_native_architectures(tmp_path, monkeypatch):
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci-containers.yml").read_text())
    steps = workflow["jobs"]["changes"]["steps"]
    matrix = next(step for step in steps if step.get("id") == "matrix")
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("A13N_SERVICE_CHANGED", "false")
    monkeypatch.setenv("BROWSER_CHANGED", "false")
    monkeypatch.setenv("SANDBOX_CHANGED", "true")
    subprocess.run(["bash", "-eu", "-c", matrix["run"]], check=True)
    images = json.loads(output.read_text().removeprefix("images="))
    assert {image["runner"] for image in images} == {"ubuntu-24.04", "ubuntu-24.04-arm"}
    assert len(images) == 6
    assert len({image["cache_scope"] for image in images}) == 6
    binaries = workflow["jobs"]["sandbox-binaries"]
    assert binaries["uses"] == "./.github/workflows/sandbox-binary.yml"
    assert {item["arch"] for item in binaries["strategy"]["matrix"]["include"]} == {"amd64", "arm64"}
    assert workflow["jobs"]["containers"]["needs"] == ["changes", "sandbox-binaries"]
    build_steps = workflow["jobs"]["containers"]["steps"]
    download = next(step for step in build_steps if step.get("uses", "").startswith("actions/download-artifact@"))
    assert download["with"]["name"] == "sandbox-binary-${{ matrix.arch }}-${{ matrix.envd_variant }}"
    assert download["with"]["path"] == "tmp/sandbox-binaries/${{ matrix.arch }}"
    build = next(step for step in build_steps if step.get("uses", "").startswith("docker/build-push-action@"))
    assert "if" not in build
    assert build["with"]["load"] is True
    assert build["with"]["file"] == "${{ matrix.dockerfile }}"
    assert build["with"]["tags"] == "${{ matrix.tag }}"
    assert build["with"]["build-args"] == "${{ matrix.build_args }}"
    assert "matrix.cache_scope" in build["with"]["cache-from"]
    assert "matrix.cache_scope" in build["with"]["cache-to"]
    for runner in ("ubuntu-24.04", "ubuntu-24.04-arm"):
        selected = [image for image in images if image["runner"] == runner]
        assert {image["dockerfile"] for image in selected} == {"deploy/docker/images/sandbox/Dockerfile"}
        assert {(image["tag"], image["build_args"]) for image in selected} == {
            ("a13n-sandbox:local", "SANDBOX_VARIANT=standard\nENVD_BINARY_SOURCE=prebuilt\nENVD_VARIANT=slim"),
            ("a13n-sandbox:local-slim", "SANDBOX_VARIANT=slim\nENVD_BINARY_SOURCE=prebuilt\nENVD_VARIANT=slim"),
            (
                "a13n-sandbox:local-desktop",
                "SANDBOX_VARIANT=desktop\nENVD_BINARY_SOURCE=prebuilt\nENVD_VARIANT=default",
            ),
        }
        assert {image["check_target"] for image in selected} == {
            "image-check-sandbox",
            "image-check-sandbox-slim",
            "image-check-sandbox-desktop",
        }
    filters = yaml.safe_load(next(step for step in steps if step.get("id") == "filter")["with"]["filters"])
    for name in (
        "deploy/docker/images/sandbox/init-skills.sh",
        "deploy/docker/images/sandbox/entrypoint.sh",
        "crates/a13n-envd/src/daemon_supervisor.rs",
        "deploy/docker/images/sandbox/desktop.py",
        "packages/a13n-service/a13n_service/providers/environment/desktop_guest.py",
        "scripts/sandbox_smoke.py",
    ):
        assert any(Path(name).full_match(pattern) for pattern in workflow[True]["pull_request"]["paths"])
        assert any(Path(name).full_match(pattern) for pattern in filters["sandbox"])
    makefile = (ROOT / "Makefile").read_text()
    assert 'scripts/sandbox_smoke.py --image "$(SANDBOX_IMAGE)" --variant standard' in makefile
    assert 'scripts/sandbox_smoke.py --image "$(SANDBOX_SLIM_IMAGE)" --variant slim' in makefile

    assert 'scripts/sandbox_smoke.py --image "$(SANDBOX_DESKTOP_IMAGE)" --variant desktop' in makefile


@pytest.mark.parametrize("failed", ["none", "compose", "quickstart", "both"])
def test_parallel_compose_checks_wait_for_both_and_propagate_failures(tmp_path, monkeypatch, failed):
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci-containers.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["containers"]["steps"]
        if step["name"] == "Check independent Compose stacks concurrently"
    )
    executable = tmp_path / "python3"
    executable.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" > "$2.args"\n'
        "sleep 0.05\n"
        'touch "$2.finished"\n'
        'if [ "$FAILED" = "$2" ] || [ "$FAILED" = both ]; then exit 1; fi\n'
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAILED", failed)
    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", step["run"]], cwd=tmp_path)
    assert (result.returncode == 0) == (failed == "none")
    for target, port in (("compose", 18080), ("quickstart", 18081)):
        assert (tmp_path / f"{target}.finished").exists()
        assert (tmp_path / f"{target}.args").read_text().strip() == (f"scripts/deploy_smoke.py {target} --port {port}")


def test_sandbox_source_and_development_builds_select_optional_vm_backends():
    workflow = yaml.safe_load((ROOT / ".github/workflows/images.yml").read_text())
    job = workflow["jobs"]["sandbox-binaries"]
    step = next(step for step in job["steps"] if "cargo build" in step.get("run", ""))
    assert '--features "$features"' in step["run"]
    assert "features=vm" in step["run"]
    assert set(job["strategy"]["matrix"]["envd-variant"]) == {"default", "slim", "vm"}
    assert step["env"]["BOXLITE_DEPS_STUB"] == "2"
    assert step["env"]["BOXLITE_EMBED_RUNTIME"] == "1"
    assert any("protobuf-compiler" in step.get("run", "") for step in job["steps"])
    dockerfile = (ROOT / "deploy/docker/images/sandbox/Dockerfile").read_text()
    assert "ARG ENVD_VARIANT=auto" in dockerfile
    assert "vm) features=vm" in dockerfile
    assert "ENV BOXLITE_DEPS_STUB=2 BOXLITE_EMBED_RUNTIME=1" in dockerfile
    assert "protobuf-compiler" in dockerfile


def test_shared_sandbox_binary_is_source_keyed_and_required_by_ci_consumers():
    path = ".github/workflows/sandbox-binary.yml"
    workflow = yaml.safe_load((ROOT / path).read_text())
    steps = workflow["jobs"]["build"]["steps"]
    cache = next(step for step in steps if step.get("id") == "binary")
    assert "restore-keys" not in cache["with"]
    for source in (path, "Cargo.toml", "Cargo.lock", "crates/a13n-envd/**"):
        assert source in cache["with"]["key"]
    build = next(step for step in steps if "cargo build" in step.get("run", ""))
    assert "--release --locked" in build["run"]
    assert '--no-default-features --features "$features"' in build["run"]
    assert "inputs.variant" in cache["with"]["key"]
    assert workflow[True]["workflow_call"]["inputs"]["variant"]["default"] == "default"
    assert workflow["env"]["BOXLITE_DEPS_STUB"] == "2"
    assert workflow["env"]["BOXLITE_EMBED_RUNTIME"] == "1"
    assert build["if"] == "steps.binary.outputs.cache-hit != 'true'"
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert "if" not in upload and upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["name"] == "sandbox-binary-${{ inputs.arch }}-${{ inputs.variant }}"
    for name in ("ci-a13n-service-e2e.yml", "ci-containers.yml"):
        consumer = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
        assert path in consumer[True]["pull_request"]["paths"]
        assert any(job.get("uses") == "./" + path for job in consumer["jobs"].values())
