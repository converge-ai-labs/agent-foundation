from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github/workflows/release-a13n-harness-ui.yml"


def test_publication_requires_both_compatibility_endpoints() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    compatibility = jobs["compatibility"]
    assert compatibility["needs"] == "build-python"
    assert compatibility["strategy"]["matrix"]["dependencies"] == ["minimum", "latest"]
    assert "compatibility" in jobs["publish-python"]["needs"]
    assert "if" not in jobs["publish-python"]
    assert "continue-on-error" not in compatibility
    smoke = next(
        step
        for step in compatibility["steps"]
        if step["name"] == "Verify installed application and matching native runtime"
    )
    assert "if" not in smoke
    assert "continue-on-error" not in smoke
    assert "--constraint minimum-constraints.txt" in smoke["run"]
    assert "--resolution highest" in smoke["run"]
    assert "anyio.run(runtime.resolve)" in smoke["run"]


def test_minimum_constraints_come_from_owning_manifests(tmp_path: Path) -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    step = next(
        step for step in jobs["compatibility"]["steps"] if step["name"] == "Resolve declared dependency minimums"
    )
    assert step["if"] == "matrix.dependencies == 'minimum'"
    for relative in (
        "scripts/release_version.py",
        "packages/a13n-environment/pyproject.toml",
        "packages/a13n-harness-ui/pyproject.toml",
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    # Changing a policy floor must change the smoke constraints without editing this workflow.
    manifest = tmp_path / "packages/a13n-environment/pyproject.toml"
    manifest.write_text(manifest.read_text().replace(">=0.0.5,<0.1.0", ">=0.0.6,<0.1.0"))
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", step["run"]], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert set((tmp_path / "minimum-constraints.txt").read_text().splitlines()) == {
        "a13n-envd-client==0.0.6",
        "a13n-environment==0.0.5",
        "a13n-harness==0.0.5",
        "a13n-stream-protocol==0.0.5",
        "a13n-logging==0.1.0",
    }


def test_release_shell_steps_parse() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    for job in jobs.values():
        for step in job["steps"]:
            if "run" in step:
                result = subprocess.run(["bash", "-n"], input=step["run"], capture_output=True, text=True)
                assert result.returncode == 0, f"{step['name']}: {result.stderr}"
