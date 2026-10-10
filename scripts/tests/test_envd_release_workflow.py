from __future__ import annotations

import os
import subprocess
import tarfile
from graphlib import TopologicalSorter
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).parents[2] / ".github/workflows/release-a13n-envd.yml"
TARGETS = {
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
    "x86_64-apple-darwin",
    "aarch64-apple-darwin",
    "x86_64-pc-windows-msvc",
    "aarch64-pc-windows-msvc",
}


@pytest.fixture
def jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def test_client_publication_waits_for_downloads_without_a_registry_cycle(jobs: dict) -> None:
    graph = {}
    for name, job in jobs.items():
        needs = job.get("needs", [])
        graph[name] = {needs} if isinstance(needs, str) else set(needs)
        assert graph[name] <= jobs.keys()
    order = list(TopologicalSorter(graph).static_order())

    assert graph["create-release"] == {"prepare", "build-client", "build-binaries"}
    assert "create-release" in graph["publish-client"]
    assert order.index("create-release") < order.index("publish-client")
    assert {"publish-crate", "publish-client"} <= graph["publish-image"]
    assert "if" not in jobs["publish-client"]
    release_steps = jobs["create-release"]["steps"]
    assert release_steps[-1]["name"] == "Create release with channel-scoped notes"
    assert "if" not in release_steps[-1]
    assert "continue-on-error" not in release_steps[-1]
    assert "continue-on-error" not in jobs["create-release"]


def test_release_keeps_all_native_targets_and_one_version_patch(jobs: dict) -> None:
    targets = jobs["build-binaries"]["strategy"]["matrix"]["include"]
    assert len(targets) == 6
    assert {entry["target"] for entry in targets} == TARGETS
    for entry in targets:
        if entry["target"].endswith("-unknown-linux-gnu"):
            assert entry["os"] == "ubuntu-24.04"
            assert entry["cross_linux"] == (entry["target"] == "aarch64-unknown-linux-gnu")
    for name in ("build-client", "build-binaries", "publish-crate"):
        patch_steps = [step for step in jobs[name]["steps"] if step["name"] == "Download release version patch"]
        assert len(patch_steps) == 1
        assert patch_steps[0]["with"]["name"] == "release-version-a13n-envd"


def test_sandbox_release_consumes_same_workflow_linux_artifacts(jobs: dict) -> None:
    assert "build-binaries" in jobs["publish-image"]["needs"]
    steps = jobs["publish-image"]["steps"]
    download = next(step for step in steps if step["name"] == "Download Linux binary archives")
    assert download["uses"] == "actions/download-artifact@v4"
    assert download["with"] == {
        "pattern": "a13n-envd-*-unknown-linux-gnu",
        "path": "dist/linux",
        "merge-multiple": True,
    }
    stage = next(step for step in steps if step["name"] == "Stage sandbox binaries")
    assert stage["env"] == {"VERSION": "${{ needs.prepare.outputs.version }}"}
    build = next(step for step in steps if step.get("uses", "").startswith("docker/build-push-action"))
    assert "ENVD_BINARY_SOURCE=prebuilt" in build["with"]["build-args"]
    assert build["with"]["context"] == "."
    assert steps.index(download) < steps.index(stage) < steps.index(build)
    assert not any("release version patch" in step["name"] for step in steps)


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3-rc.1"])
@pytest.mark.parametrize("missing_arch", [None, "amd64", "arm64"])
def test_sandbox_binary_staging_preserves_target_bytes_and_requires_both_archives(
    jobs: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str, missing_arch: str | None
) -> None:
    archives = tmp_path / "dist/linux"
    archives.mkdir(parents=True)
    targets = {"amd64": "x86_64-unknown-linux-gnu", "arm64": "aarch64-unknown-linux-gnu"}
    for arch, target in targets.items():
        if arch == missing_arch:
            continue
        binary = tmp_path / f"binary-{arch}"
        binary.write_bytes(f"release binary for {target}".encode())
        binary.chmod(0o755)
        with tarfile.open(archives / f"a13n-envd-{version}-{target}.tar.gz", "w:gz") as archive:
            archive.add(binary, arcname="a13n-envd")
    monkeypatch.setenv("VERSION", version)
    script = next(step["run"] for step in jobs["publish-image"]["steps"] if step["name"] == "Stage sandbox binaries")

    result = run_step(script, tmp_path)

    assert (result.returncode == 0) == (missing_arch is None), result.stderr
    if missing_arch is None:
        for arch, target in targets.items():
            binary = tmp_path / "tmp/sandbox-binaries" / arch / "a13n-envd"
            assert binary.read_bytes() == f"release binary for {target}".encode()
            assert os.access(binary, os.X_OK)


def release_step(jobs: dict, name: str) -> str:
    return next(step["run"] for step in jobs["create-release"]["steps"] if step["name"] == name)


def run_step(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("existing", [False, True])
def test_release_creation_reuses_existing_assets_without_replacing_them(
    jobs: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for command, exit_code in (("gh", 0 if existing else 1), ("python3", 0)):
        path = fake_bin / command
        path.write_text(f'#!/bin/sh\necho "{command} $*" >> commands\nexit {exit_code}\n', encoding="utf-8")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GITHUB_REF_NAME", "release/a13n-envd-v1.2.3")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repository")
    monkeypatch.setenv("VERSION", "1.2.3")

    result = run_step(release_step(jobs, "Create release with channel-scoped notes"), tmp_path)

    assert result.returncode == 0, result.stderr
    commands = (tmp_path / "commands").read_text(encoding="utf-8").splitlines()
    assert commands[0] == "gh release view release/a13n-envd-v1.2.3 --repo example/repository"
    assert len(commands) == (1 if existing else 2)
    if not existing:
        assert commands[1].startswith("python3 scripts/create-github-release.py a13n-envd 1.2.3 ")


@pytest.mark.parametrize("status", ["200", "404", "503"])
def test_crate_publication_is_selected_by_version(
    jobs: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text(f'#!/bin/sh\necho "$*" >> requests\nprintf {status}\n', encoding="utf-8")
    curl.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GITHUB_RUN_ID", "test")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("VERSION", "1.2.3")
    script = next(
        step["run"] for step in jobs["publish-crate"]["steps"] if step["name"] == "Resolve crate publication state"
    )

    result = run_step(script, tmp_path)

    requests = (tmp_path / "requests").read_text().splitlines()
    assert len(requests) == 1
    assert requests[0].endswith("https://crates.io/api/v1/crates/a13n-envd/1.2.3")
    if status == "503":
        assert result.returncode != 0
    else:
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "output").read_text().strip() == f"published={str(status == '200').lower()}"


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3-rc.1"])
@pytest.mark.parametrize("binary_version", ["1.2.3", "1.2.3-rc.1", "0.0.0"])
def test_native_release_checks_binary_version_without_inner_isolation(
    jobs: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str, binary_version: str
) -> None:
    steps = jobs["build-binaries"]["steps"]
    step = next(step for step in steps if step["name"] == "Verify native Linux release version")
    assert step["if"] == "matrix.target == 'x86_64-unknown-linux-gnu'"
    assert step["env"] == {
        "TARGET": "${{ matrix.target }}",
        "VERSION": "${{ needs.prepare.outputs.version }}",
    }
    assert all("isolation probe" not in step.get("run", "") for step in steps)
    target = "x86_64-unknown-linux-gnu"
    binary = tmp_path / "target" / target / "release/a13n-envd"
    binary.parent.mkdir(parents=True)
    binary.write_text(f'#!/bin/sh\n[ "$*" = "--version" ] || exit 1\necho "a13n-envd {binary_version}"\n')
    binary.chmod(0o755)
    monkeypatch.setenv("TARGET", target)
    monkeypatch.setenv("VERSION", version)

    result = run_step(step["run"], tmp_path)

    assert (result.returncode == 0) == (version == binary_version)
