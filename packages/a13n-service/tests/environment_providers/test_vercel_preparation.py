"""Real POSIX-shell preparation dispatch: one job, frozen revision and terminal failures."""

import hashlib
import json
import subprocess
import time

import pytest
from a13n_service.providers.environments.vercel.preparation import dispatch_script, specification


def digest(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def dispatch(root, manifest, expected=None):
    result = subprocess.run(
        ["sh", "-c", dispatch_script(manifest, expected or digest(manifest), root=str(root))],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    return result.stdout


def settle(root):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        path = root / "status"
        if path.exists() and path.read_text() in {"ready", "failed"}:
            return path.read_text()
        time.sleep(0.01)
    pytest.fail("guest job did not settle")


def test_rejoins_original_script_after_deployment_change(tmp_path):
    root = tmp_path / "prepare"
    manifest = {"image": "test", "script": "sleep .1; printf x >> count"}
    assert dispatch(root, manifest) == "running"
    changed = {"image": "test", "script": "printf unexpected >> count"}
    assert dispatch(root, changed, digest(manifest)) == "running"
    assert settle(root) == "ready"
    assert dispatch(root, changed, digest(manifest)) == "ready"
    assert (root / "count").read_text() == "x"
    assert (root / "revision").read_text() == digest(manifest)


def test_changed_revision_cannot_launch_or_overwrite_installation(tmp_path):
    root = tmp_path / "prepare"
    manifest = {"image": "test", "script": "true"}
    assert dispatch(root, manifest, "a" * 64) == "incompatible"
    assert not (root / "install.sh").exists()
    assert dispatch(root, manifest) == "running"
    assert settle(root) == "ready"
    assert dispatch(root, manifest, "b" * 64) == "conflict"


@pytest.mark.parametrize("script", ["printf x >> count; exit 1", "printf x >> count; kill -KILL $$"])
def test_failed_or_crashed_job_is_not_reinstalled(tmp_path, script):
    root = tmp_path / "prepare"
    manifest = {"image": "test", "script": script}
    assert dispatch(root, manifest) == "running"
    assert settle(root) == "failed"
    assert dispatch(root, manifest) == "failed"
    assert (root / "count").read_text() == "x"


def test_lost_guest_process_is_terminal_not_silently_retried(tmp_path):
    root = tmp_path / "prepare"
    manifest = {"image": "test", "script": "printf unexpected > count"}
    root.mkdir()
    (root / "revision").write_text(digest(manifest))
    (root / "status").write_text("running")
    (root / "launch.sh").write_text("exec 9>install.lock; flock -n 9 || exit 0; printf failed > status")
    assert dispatch(root, manifest) == "running"
    assert settle(root) == "failed"
    assert not (root / "count").exists()


def test_headless_installs_no_python_or_desktop_packages():
    manifest = specification(desktop=False)
    assert manifest["image"] == "vercel/sandbox/ubuntu"
    assert "python" not in manifest["script"]
    assert "jq" not in manifest["script"]
    assert "entrypoint.sh" in manifest["script"]
    assert "xvfb" not in manifest["script"]
    assert "xvfb" in specification(desktop=True)["script"]
