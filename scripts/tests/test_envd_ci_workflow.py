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


def test_linux_ci_runs_native_accessibility_and_selects_its_fixtures():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    job = workflow["jobs"]["daemon"]
    dependencies = next(step for step in job["steps"] if step["name"] == "Install Linux isolation dependency")
    for package in ["dbus-daemon", "at-spi2-core", "python3-gi", "gir1.2-gtk-3.0"]:
        assert package in dependencies["run"]
    native = next(
        step for step in job["steps"] if step["name"] == "Test native AT-SPI in an isolated desktop and user bus"
    )
    assert native["if"] == "matrix.linux"
    assert native["env"]["ENVD_FEATURES"] == "${{ matrix.features }}"
    assert "make accessibility-linux-test" in native["run"]
    events = workflow.get("on", workflow.get(True))
    filters = yaml.safe_load(workflow["jobs"]["changes"]["steps"][1]["with"]["filters"])
    for path in ["dev/fixtures/linux-desktop/**", "packages/a13n-harness/tests/test_accessibility_linux_native.py"]:
        assert path in events["pull_request"]["paths"]
        assert path in events["push"]["paths"]
        assert path in filters["daemon"]


@pytest.mark.parametrize("features", ["", "vm"])
def test_native_accessibility_make_target_uses_the_release_variant(features: str):
    completed = subprocess.run(
        ["make", "--dry-run", "accessibility-linux-test", f"ENVD_FEATURES={features}"],
        cwd=WORKFLOW.parents[2],
        capture_output=True,
        text=True,
        check=True,
    )
    commands = completed.stdout
    assert f'cargo build --release --locked --package a13n-envd --features "{features}"' in commands
    assert (
        f'cargo test --release --locked --package a13n-envd --features "{features}" --lib accessibility::linux'
        in commands
    )
    assert '/target/release/a13n-envd"' in commands
    assert "target/debug" not in commands


def test_cgroup_monitoring_uses_current_release_binary():
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["daemon"]
    step = next(step for step in job["steps"] if step["name"] == "Test current Envd binary against real cgroup limits")
    script = step["run"]
    assert "target/debug" not in script
    assert "src=$PWD/target/release/a13n-envd,dst=/test-bin/a13n-envd,readonly" in script
    assert "--cpus=0.5 --memory=512m" in script
    assert "/tests/monitoring_linux.py /test-bin/a13n-envd" in script
