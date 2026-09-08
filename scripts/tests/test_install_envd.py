from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

INSTALLER = Path(__file__).parents[1] / "install-a13n-envd.sh"
pytestmark = pytest.mark.skipif(os.name == "nt" or shutil.which("jq") is None, reason="POSIX shell and jq required")
BINARY = b"native binary fixture; must not be executed\n"


@pytest.fixture
def installer(tmp_path: Path):
    """Use real tar/checksum/mv and fake only remote responses and host identity."""
    commands = tmp_path / "commands"
    commands.mkdir()
    fixtures = tmp_path / "releases"
    fixtures.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    destination = tmp_path / "bin"
    env = {key: value for key, value in os.environ.items() if not key.startswith("A13N_ENVD_")}
    env.update(
        PATH=f"{commands}{os.pathsep}{os.environ['PATH']}",
        HOME=str(home),
        SHELL="/bin/bash",
        XDG_CONFIG_HOME=str(home / ".config"),
        ZDOTDIR=str(home),
        A13N_ENVD_INSTALL_DIR=str(destination),
        FIXTURES=str(fixtures),
        MOCK_OS="Linux",
        MOCK_ARCH="x86_64",
    )
    curl = f"""#!{sys.executable}
import os, pathlib, shutil, sys
from urllib.parse import urlparse, parse_qs, unquote
args = sys.argv[1:]
url = next(a for a in args if a.startswith('https://'))
root = pathlib.Path(os.environ['FIXTURES'])
with (root / 'requests').open('a') as log:
    log.write(url + '\\n')
parsed = urlparse(url)
if parsed.hostname == 'api.github.com':
    source = root / ('page-' + parse_qs(parsed.query)['page'][0] + '.json')
else:
    source = root / unquote(parsed.path).rsplit('/', 1)[-1]
shutil.copyfile(source, args[args.index('-o') + 1])
"""
    for name, content in {
        "curl": curl,
        "uname": '#!/bin/sh\ncase "$1" in -s) echo "$MOCK_OS" ;; -m) echo "$MOCK_ARCH" ;; esac\n',
        "id": "#!/bin/sh\necho 1000\n",
    }.items():
        path = commands / name
        path.write_text(content)
        path.chmod(0o755)

    def run(*args: str, **environment: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(INSTALLER), *args],
            env=env | environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )

    return run, fixtures, destination, home, commands


def release(fixtures: Path, version: str = "0.0.6", target: str = "x86_64-unknown-linux-gnu", *, bad: str = "") -> Path:
    archive = fixtures / f"a13n-envd-{version}-{target}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        names = ["a13n-envd", "LICENSE"]
        if bad == "traversal":
            names.append("../outside")
        if bad == "duplicate":
            names.append("a13n-envd")
        if bad == "missing":
            names.remove("a13n-envd")
        for name in names:
            data = BINARY if name == "a13n-envd" else b"license\n"
            entry = tarfile.TarInfo(name)
            if name == "a13n-envd" and bad in {"symlink", "hardlink"}:
                entry.type = tarfile.SYMTYPE if bad == "symlink" else tarfile.LNKTYPE
                entry.linkname = "LICENSE"
                tar.addfile(entry)
            else:
                if bad == "empty" and name == "a13n-envd":
                    data = b""
                entry.size = len(data)
                tar.addfile(entry, io.BytesIO(data))
    (fixtures / "SHA256SUMS").write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    return archive


def assert_failed(result, destination: Path):
    assert result.returncode != 0, result.stdout
    assert not list(destination.glob(".a13n-envd.*"))


@pytest.mark.parametrize(
    ("system", "machine", "target"),
    [
        ("Linux", "x86_64", "x86_64-unknown-linux-gnu"),
        ("Linux", "aarch64", "aarch64-unknown-linux-gnu"),
        ("Darwin", "x86_64", "x86_64-apple-darwin"),
        ("Darwin", "arm64", "aarch64-apple-darwin"),
    ],
)
def test_install_and_replace_without_path_mutation(installer, system, machine, target):
    run, fixtures, destination, home, _ = installer
    release(fixtures, target=target)
    destination.mkdir()
    (destination / "a13n-envd").write_bytes(b"old binary")
    for _ in range(2):
        result = run("--version", "0.0.6", MOCK_OS=system, MOCK_ARCH=machine)
        assert result.returncode == 0, result.stderr
        assert (destination / "a13n-envd").read_bytes() == BINARY
        assert (destination / "a13n-envd").stat().st_mode & 0o777 == 0o755
        assert not list(destination.glob(".a13n-envd.*"))
    assert not list(home.iterdir())
    assert "release/a13n-envd-v0.0.6/" in (fixtures / "requests").read_text()


def test_default_version_paginates_and_excludes_other_components_and_prereleases(installer):
    run, fixtures, destination, _, _ = installer
    release(fixtures)
    rows = [
        {"tag_name": "release/a13n-harness-v9.0.0", "draft": False, "prerelease": False},
        {"tag_name": "release/a13n-envd-v0.0.7-rc.1", "draft": False, "prerelease": False},
        {"tag_name": "release/a13n-envd-v0.0.8", "draft": False, "prerelease": True},
        {"tag_name": "release/a13n-envd-v0.0.9", "draft": True, "prerelease": False},
    ]
    (fixtures / "page-1.json").write_text(json.dumps(rows))
    (fixtures / "page-2.json").write_text(
        json.dumps([{"tag_name": "release/a13n-envd-v0.0.6", "draft": False, "prerelease": False}])
    )
    result = run()
    assert result.returncode == 0, result.stderr
    assert (destination / "a13n-envd").read_bytes() == BINARY
    assert "page=2" in (fixtures / "requests").read_text()


@pytest.mark.parametrize("response", ["[]", "{}", "not json"])
def test_no_stable_release_or_invalid_response(installer, response):
    run, fixtures, destination, _, _ = installer
    (fixtures / "page-1.json").write_text(response)
    assert_failed(run(), destination)
    assert not (destination / "a13n-envd").exists()


def test_explicit_rc_and_flags_override_environment(installer):
    run, fixtures, destination, home, _ = installer
    release(fixtures, version="0.0.6-rc.1")
    result = run(
        "--version",
        "0.0.6-rc.1",
        "--install-dir",
        str(destination),
        "--no-add-to-path",
        A13N_ENVD_VERSION="bad",
        A13N_ENVD_INSTALL_DIR="relative",
        A13N_ENVD_ADD_TO_PATH="bad",
    )
    assert result.returncode == 0, result.stderr
    assert "api.github.com" not in (fixtures / "requests").read_text()
    assert not list(home.iterdir())


def test_environment_version_and_default_nonroot_directory(installer):
    run, fixtures, _, home, _ = installer
    release(fixtures)
    result = run(A13N_ENVD_INSTALL_DIR="", A13N_ENVD_VERSION="0.0.6")
    assert result.returncode == 0, result.stderr
    assert (home / ".local/bin/a13n-envd").read_bytes() == BINARY


@pytest.mark.parametrize("version", ["latest", "0.0.6rc1", "0.0.6-rc.0", "01.2.3", "1.2", "1.2.3\nevil"])
def test_invalid_versions_fail_before_download(installer, version):
    run, fixtures, destination, _, _ = installer
    assert_failed(run("--version", version), destination)
    assert not (fixtures / "requests").exists()


@pytest.mark.parametrize(
    "args", [("--unknown",), ("--version",), ("--install-dir", "relative"), ("--add-to-path", "--no-add-to-path")]
)
def test_invalid_arguments(installer, args):
    run, fixtures, destination, _, _ = installer
    assert_failed(run(*args), destination)
    assert not (fixtures / "requests").exists()


@pytest.mark.parametrize("bad", ["missing", "duplicate", "mismatch", "invalid"])
def test_checksum_failure_preserves_existing_binary(installer, bad):
    run, fixtures, destination, _, _ = installer
    archive = release(fixtures)
    checksums = fixtures / "SHA256SUMS"
    if bad == "missing":
        checksums.write_text("0" * 64 + "  another-archive.tar.gz\n")
    elif bad == "duplicate":
        checksums.write_text(checksums.read_text() * 2)
    elif bad == "mismatch":
        checksums.write_text("0" * 64 + f"  {archive.name}\n")
    else:
        checksums.write_text(f"invalid  {archive.name}\n")
    destination.mkdir()
    (destination / "a13n-envd").write_bytes(b"old")
    assert_failed(run("--version", "0.0.6"), destination)
    assert (destination / "a13n-envd").read_bytes() == b"old"


@pytest.mark.parametrize("bad", ["traversal", "duplicate", "missing", "symlink", "hardlink", "empty", "corrupt"])
def test_malformed_verified_archive_does_not_replace(installer, bad):
    run, fixtures, destination, _, _ = installer
    archive = release(fixtures, bad=bad)
    if bad == "corrupt":
        archive.write_bytes(b"not a tar archive")
        (fixtures / "SHA256SUMS").write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    destination.mkdir()
    (destination / "a13n-envd").write_bytes(b"old")
    assert_failed(run("--version", "0.0.6"), destination)
    assert (destination / "a13n-envd").read_bytes() == b"old"
    assert not (destination / "outside").exists()


def test_failed_atomic_move_preserves_previous_install(installer):
    run, fixtures, destination, _, commands = installer
    release(fixtures)
    move = commands / "mv"
    move.write_text("#!/bin/sh\nexit 1\n")
    move.chmod(0o755)
    destination.mkdir()
    (destination / "a13n-envd").write_bytes(b"old")
    assert_failed(run("--version", "0.0.6"), destination)
    assert (destination / "a13n-envd").read_bytes() == b"old"


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_destination_is_not_followed(installer, kind):
    run, fixtures, destination, _, _ = installer
    release(fixtures)
    destination.mkdir()
    if kind == "directory":
        (destination / "a13n-envd").mkdir()
    else:
        (destination / "a13n-envd").symlink_to(fixtures / "SHA256SUMS")
    assert_failed(run("--version", "0.0.6"), destination)
    assert not (fixtures / "requests").exists()


@pytest.mark.parametrize(
    ("shell", "profile"),
    [("bash", ".bashrc"), ("zsh", ".zshrc"), ("sh", ".profile"), ("fish", ".config/fish/config.fish")],
)
def test_path_opt_in_is_idempotent_and_quotes_path(installer, shell, profile):
    run, fixtures, destination, home, _ = installer
    release(fixtures)
    destination = destination / "quoted ' $(touch BAD) path"
    for _ in range(2):
        result = run("--version", "0.0.6", "--install-dir", str(destination), "--add-to-path", SHELL=f"/bin/{shell}")
        assert result.returncode == 0, result.stderr
    content = (home / profile).read_text()
    assert content.count("# a13n-envd standalone installer") == 1
    if shell != "fish":
        result = subprocess.run(
            ["sh", "-c", '. "$1"; printf "%s" "$PATH"', "sh", str(home / profile)],
            cwd=home,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.split(os.pathsep)[0] == str(destination)
        assert not (home / "BAD").exists()


def test_unsupported_target_and_help_have_no_side_effects(installer):
    run, fixtures, destination, _, _ = installer
    assert_failed(run("--version", "0.0.6", MOCK_ARCH="i686"), destination)
    assert run("--help", MOCK_OS="Unknown").returncode == 0
    assert not (fixtures / "requests").exists()
    assert not destination.exists()
