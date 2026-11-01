"""Browser runtime image routing uses Service ownership and the existing channels."""

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


@pytest.mark.parametrize("name", ["images.yml", "ci-containers.yml"])
def test_browser_image_has_input_filter_and_selectable_matrix(name, tmp_path):
    document = workflow(name)
    steps = document["jobs"]["changes"]["steps"]
    filters = yaml.safe_load(next(step for step in steps if step.get("id") == "filter")["with"]["filters"])
    assert "deploy/docker/images/browser/**" in filters["browser"]
    assert "packages/a13n-service/a13n_service/providers/environment/desktop_guest.py" in filters["browser"]
    matrix = next(step for step in steps if step.get("id") == "matrix")
    output = tmp_path / "output"
    environment = dict(os.environ, GITHUB_EVENT_NAME="push", GITHUB_OUTPUT=str(output))
    environment.update({key: "false" for key in matrix["env"]})
    environment["BROWSER_CHANGED"] = "true"
    subprocess.run(["bash", "-e", "-c", matrix["run"]], env=environment, check=True)
    images = json.loads(output.read_text().removeprefix("images="))
    assert images and all(image["name"].startswith("a13n-browser") for image in images)
    if name == "images.yml":
        assert images[0]["file"] == "deploy/docker/images/browser/Dockerfile"
        assert images[0]["tag"] == "dev"
        assert images[0]["platforms"] == "linux/amd64,linux/arm64"
    else:
        assert {image["runner"] for image in images} == {"ubuntu-24.04", "ubuntu-24.04-arm"}
        assert all(image["check_target"] == "image-check-browser" for image in images)
        tools = document["jobs"]["containers"]["steps"]
        assert any(step.get("uses", "").startswith("astral-sh/setup-uv@") for step in tools)
        assert any(step.get("uses", "").startswith("actions/setup-python@") for step in tools)


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3-rc.1"])
@pytest.mark.parametrize("image", ["a13n-service", "a13n-browser"])
def test_service_companion_image_release_tags(version, image, tmp_path):
    job = workflow("release-a13n-service.yml")["jobs"]["publish-image"]
    entries = job["strategy"]["matrix"]["include"]
    assert {entry["name"] for entry in entries} == {"a13n-service", "a13n-browser"}
    assert (
        next(entry for entry in entries if entry["name"] == "a13n-browser")["file"]
        == "deploy/docker/images/browser/Dockerfile"
    )
    step = next(step for step in job["steps"] if step.get("id") == "image-tags")
    output = tmp_path / "output"
    name = "ghcr.io/converge-ai-labs/" + image
    subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env=dict(os.environ, IMAGE=name, VERSION=version, GITHUB_OUTPUT=str(output)),
        check=True,
    )
    tags = output.read_text().splitlines()[1:-1]
    assert tags == [f"{name}:{version}"] + ([] if "-rc." in version else [f"{name}:latest"])
    build = next(step for step in job["steps"] if step.get("uses", "").startswith("docker/build-push-action@"))
    assert build["with"]["file"] == "${{ matrix.file }}"
    assert "matrix.name" in build["with"]["cache-to"]


def test_browser_runtime_uses_service_locked_websocket_client():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    version = next(package["version"] for package in lock["package"] if package["name"] == "websockets")
    dockerfile = (ROOT / "deploy/docker/images/browser/Dockerfile").read_text()
    assert f"python3 -m pip install --no-cache-dir websockets=={version}" in dockerfile
    service = tomllib.loads((ROOT / "packages/a13n-service/pyproject.toml").read_text())
    assert "websockets>=15,<16" in service["project"]["dependencies"]
