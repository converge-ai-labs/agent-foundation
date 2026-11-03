"""Browser runtime image routing uses Service ownership and the existing channels."""

import json
import os
import subprocess
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


def test_browser_latest_workflow_builds_tests_and_publishes_same_artifacts():
    document = workflow("browser-image.yml")
    triggers = document[True]  # PyYAML parses the YAML 1.1 key `on` as True.
    assert {"push", "pull_request", "schedule", "workflow_dispatch"} <= triggers.keys()
    assert triggers["push"]["branches"] == ["main"]
    concurrency = document["concurrency"]
    assert "'publish'" in concurrency["group"]
    assert concurrency["cancel-in-progress"] is False
    build = document["jobs"]["build"]
    assert {entry["arch"] for entry in build["strategy"]["matrix"]["include"]} == {"amd64", "arm64"}
    steps = build["steps"]
    commands = [step.get("run", "") for step in steps]
    build_index = next(i for i, command in enumerate(commands) if "docker build --pull --no-cache" in command)
    smoke_index = next(i for i, command in enumerate(commands) if "make image-check-browser" in command)
    push_index = next(i for i, command in enumerate(commands) if "docker push" in command)
    assert build_index < smoke_index < push_index
    assert "a13n-browser:ci" in commands[build_index] and "a13n-browser:ci" in commands[smoke_index]
    assert "docker tag a13n-browser:ci" in commands[push_index]
    assert "github.ref == 'refs/heads/main'" in steps[push_index]["if"]
    publish = document["jobs"]["publish"]
    assert publish["needs"] == "build" and "github.ref == 'refs/heads/main'" in publish["if"]
    manifest = next(step["run"] for step in publish["steps"] if "imagetools create" in step.get("run", ""))
    assert '"$IMAGE:$tag-amd64" "$IMAGE:$tag-arm64"' in manifest
    assert "github.run_id" in manifest and "github.run_attempt" in manifest
    assert "commits/main" in manifest and '"$IMAGE:latest"' in manifest
