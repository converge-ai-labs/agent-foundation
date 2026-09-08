from __future__ import annotations

import os
import subprocess
import sys
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
    assert release_steps[-1]["name"] == "Verify public release downloads"
    assert "if" not in release_steps[-1]
    assert "continue-on-error" not in release_steps[-1]
    assert "continue-on-error" not in jobs["create-release"]


def test_release_keeps_all_native_targets_and_one_version_patch(jobs: dict) -> None:
    targets = jobs["build-binaries"]["strategy"]["matrix"]["include"]
    assert len(targets) == 6
    assert {entry["target"] for entry in targets} == TARGETS
    for name in ("build-client", "build-binaries", "publish-crate", "publish-image"):
        patch_steps = [step for step in jobs[name]["steps"] if step["name"] == "Download release version patch"]
        assert len(patch_steps) == 1
        assert patch_steps[0]["with"]["name"] == "release-version-a13n-envd"


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


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3-rc.1"])
@pytest.mark.parametrize(
    "failure", [None, "missing-archive", "corrupt-archive", "missing-checksums", "corrupt-checksums"]
)
def test_public_download_verification_fails_closed(
    jobs: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str, failure: str | None
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    assets = [
        f"a13n-envd-{version}-{target}.{'zip' if 'windows' in target else 'tar.gz'}" for target in sorted(TARGETS)
    ]
    assets.extend(["client.whl", "client.tar.gz"])
    for asset in assets:
        (dist / asset).write_bytes(asset.encode())
    result = run_step(release_step(jobs, "Create checksums"), tmp_path)
    assert result.returncode == 0, result.stderr
    assets.append("SHA256SUMS")

    published = tmp_path / "published"
    published.mkdir()
    for asset in assets:
        (published / asset).write_bytes((dist / asset).read_bytes())
    if failure:
        target = "SHA256SUMS" if failure.endswith("checksums") else assets[0]
        if failure.startswith("missing"):
            (published / target).unlink()
        else:
            (published / target).write_bytes(b"wrong release bytes")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, shutil, sys\n"
        "args = sys.argv[1:]\n"
        "assert not any(arg in args for arg in ('--header', '-H', '--user', '-u'))\n"
        "assert '--fail' in args and '--max-time' in args and '--retry-max-time' in args\n"
        f"assert args[-1].startswith('https://github.com/example/repository/releases/download/release%2Fa13n-envd-v{version}/')\n"
        "filename = args[-1].rsplit('/', 1)[-1]\n"
        "source = pathlib.Path('published') / filename\n"
        "if not source.exists(): sys.exit(22)\n"
        "shutil.copyfile(source, args[args.index('--output') + 1])\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GITHUB_REF_NAME", f"release/a13n-envd-v{version}")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repository")

    result = run_step(release_step(jobs, "Verify public release downloads"), tmp_path)

    if failure:
        assert result.returncode != 0
        if failure.startswith("corrupt"):
            assert "Published release asset does not match this release tag:" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert {path.name for path in (tmp_path / "verified").iterdir()} == set(assets)


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3-rc.1"])
def test_rc_image_never_advances_latest(
    jobs: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    image = "ghcr.io/example/a13n-sandbox"
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("IMAGE", image)
    monkeypatch.setenv("VERSION", version)
    script = next(step["run"] for step in jobs["publish-image"]["steps"] if step["name"] == "Resolve image tags")

    result = run_step(script, tmp_path)

    assert result.returncode == 0, result.stderr
    lines = output.read_text(encoding="utf-8").splitlines()
    assert f"{image}:{version}" in lines
    assert (f"{image}:latest" in lines) == ("-rc." not in version)
