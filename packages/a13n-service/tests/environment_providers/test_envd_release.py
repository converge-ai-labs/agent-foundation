"""Execute the frozen cloud installer offline through the guest's POSIX shell."""

import hashlib
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from a13n_service.providers.environment import envd_release


@pytest.fixture
def release(tmp_path, monkeypatch):
    assets = tmp_path / "release"
    assets.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    curl = tools / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, shutil, sys\n"
        "url = next(a for a in sys.argv[1:] if a.startswith('https://'))\n"
        "root = pathlib.Path(os.environ['RELEASE_FIXTURE'])\n"
        "with (root / 'requests').open('a') as log: log.write(url + '\\n')\n"
        "shutil.copyfile(root / url.rsplit('/', 1)[1], sys.argv[sys.argv.index('-o') + 1])\n"
    )
    curl.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}:{os.environ['PATH']}")
    monkeypatch.setenv("RELEASE_FIXTURE", str(assets))
    installer = Path(__file__).resolve().parents[4] / "scripts/install-a13n-envd.sh"
    (assets / installer.name).write_bytes(installer.read_bytes())
    assert hashlib.sha256(installer.read_bytes()).hexdigest() == envd_release.INSTALLER_SHA256
    return assets


def prepare(release, *, prefix="", failure=None):
    name = f"a13n-envd-{envd_release.VERSION}-{envd_release.TARGET}.tar.gz"
    archive = release / name
    version = "0.0.1" if failure == "version" else envd_release.VERSION
    binary = f"#!/bin/sh\necho 'a13n-envd {version}'\n".encode()
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo("other" if failure == "executable" else "a13n-envd")
        member.size = len(binary)
        output.addfile(member, io.BytesIO(binary))
        license = tarfile.TarInfo("LICENSE")
        license.size = 7
        output.addfile(license, io.BytesIO(b"license"))
    if failure == "archive":
        archive.write_bytes(b"not a gzip archive")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if failure == "mismatch":
        digest = "0" * 64
    elif failure == "malformed":
        digest = "not-a-checksum"
    entry = f"{digest}  {prefix}{name}\n"
    checksum = release / "SHA256SUMS"
    checksum.write_text(
        f"{'a' * 64}  another-release.tar.gz\n"
        + ("" if failure == "missing" else entry)
        + (entry if failure == "duplicate" else "")
    )
    if failure == "download":
        checksum.unlink()
    return binary, name


def run_installer(destination):
    command = envd_release.installation_command(str(destination))
    assert "\n" not in command
    return subprocess.run(["sh", "-c", command], capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize("prefix", ["", "./", "*", "*./"])
def test_installer_verifies_release_and_publishes_binary(release, tmp_path, prefix):
    binary, name = prepare(release, prefix=prefix)
    destination = tmp_path / "installed envd"
    result = run_installer(destination)
    assert result.returncode == 0, result.stderr
    assert destination.read_bytes() == binary
    assert destination.stat().st_mode & 0o777 == 0o755
    base = (
        "https://github.com/converge-ai-labs/agent-foundation/releases/download/"
        f"release/a13n-envd-v{envd_release.VERSION}"
    )
    assert (release / "requests").read_text().splitlines() == [
        f"https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/{envd_release.SOURCE_REVISION}/scripts/install-a13n-envd.sh",
        f"{base}/{name}",
        f"{base}/SHA256SUMS",
    ]
    assert not list(tmp_path.glob(".a13n-envd-*"))


@pytest.mark.parametrize(
    "failure", ["download", "missing", "duplicate", "malformed", "mismatch", "archive", "executable", "version"]
)
def test_failed_install_preserves_existing_binary(release, tmp_path, failure):
    prepare(release, failure=failure)
    destination = tmp_path / "envd"
    destination.write_bytes(b"existing executable")
    destination.chmod(0o755)
    result = run_installer(destination)
    assert result.returncode != 0
    assert destination.read_bytes() == b"existing executable"
    assert destination.stat().st_mode & 0o777 == 0o755
    assert not list(tmp_path.glob(".a13n-envd-*"))


@pytest.mark.parametrize("failure", ["missing", "checksum"])
def test_unverified_installer_is_never_executed(release, tmp_path, failure):
    prepare(release)
    installer = release / "install-a13n-envd.sh"
    if failure == "missing":
        installer.unlink()
    else:
        installer.write_text('#!/bin/sh\ntouch "$RELEASE_FIXTURE/executed"\n')
    destination = tmp_path / "envd"
    destination.write_bytes(b"previous executable")
    result = run_installer(destination)
    assert result.returncode != 0
    assert destination.read_bytes() == b"previous executable"
    assert not (release / "executed").exists()
    assert not list(tmp_path.glob(".a13n-envd-*"))
