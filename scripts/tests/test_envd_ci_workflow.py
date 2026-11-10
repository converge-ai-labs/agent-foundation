from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).parents[2] / ".github/workflows/ci-a13n-envd.yml"


@pytest.mark.parametrize("component", ["PROTOCOL", "CLIENT", "DAEMON"])
@pytest.mark.parametrize("selected", [True, False])
@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped"])
def test_envd_gate_requires_exact_selected_results(component: str, selected: bool, result: str) -> None:
    gate = yaml.safe_load(WORKFLOW.read_text())["jobs"]["validation"]["steps"][0]
    env = {"CHANGES_RESULT": "success"}
    for name in ("PROTOCOL", "CLIENT", "DAEMON"):
        env[f"{name}_RESULT"] = "success"
        env[f"{name}_SELECTED"] = "true"
    env[f"{component}_RESULT"] = result
    env[f"{component}_SELECTED"] = str(selected).lower()
    completed = subprocess.run(["bash", "-e", "-c", gate["run"]], env=env, check=False)
    assert (completed.returncode == 0) == (result == ("success" if selected else "skipped"))


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
def test_envd_gate_rejects_failed_classification(result: str) -> None:
    gate = yaml.safe_load(WORKFLOW.read_text())["jobs"]["validation"]["steps"][0]
    completed = subprocess.run(["bash", "-e", "-c", gate["run"]], env={"CHANGES_RESULT": result}, check=False)
    assert completed.returncode != 0


def test_clipboard_ci_prevents_xvfb_reset_between_serial_tests():
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["daemon"]
    step = next(step for step in job["steps"] if step["name"] == "Test native X11 clipboard under Xvfb")
    assert step["if"] == "matrix.linux"
    assert 'xvfb-run -a -s "-screen 0 1280x1024x24 -noreset"' in step["run"]
    assert "--lib clipboard -- --include-ignored --test-threads=1" in step["run"]


def test_linux_ci_builds_real_embedded_vm_backends_on_both_architectures():
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["daemon"]
    assert job["env"]["BOXLITE_DEPS_STUB"] == "2"
    assert job["env"]["BOXLITE_EMBED_RUNTIME"] == "1"
    dependencies = next(step for step in job["steps"] if step["name"] == "Install Linux isolation dependency")
    assert "protobuf-compiler" in dependencies["run"]
    tests = next(step for step in job["steps"] if step["name"] == "Run a13n-envd tests")
    assert '--release --locked --package a13n-envd --features "$FEATURES"' in tests["run"]
    entries = job["strategy"]["matrix"]["include"]
    assert len(entries) == 6
    for target in ("x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu"):
        assert {(row["envd-variant"], row["features"]) for row in entries if row["target"] == target} == {
            ("default", ""),
            ("vm", "vm"),
        }
    size = next(step for step in job["steps"] if step["name"] == "Measure release binary and archive size")
    assert "envd_size.py" in size["run"]
    assert "cargo build" not in size["run"]
    assert "continue-on-error" not in size
    assert "target\\debug" not in str(job)
