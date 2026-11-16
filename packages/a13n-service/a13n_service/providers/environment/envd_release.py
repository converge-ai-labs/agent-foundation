"""Render the pinned shell installer; downloads execute inside the target or build."""

import shlex
from pathlib import PurePosixPath

VERSION = "0.8.2"
TARGET = "x86_64-unknown-linux-gnu"
SOURCE_REVISION = "06485312e6b0241121462a9da4e062789b579706"
INSTALLER_SHA256 = "37a60f35e43a6a48596a76a5699e41348233d637afefd15712dc93eb76b14c52"


def download_command(url: str, destination: str, digest: str) -> str:
    """Fetch one immutable public asset and verify it before use."""
    checksum = shlex.quote(f"{digest}  {destination}")
    url, destination = shlex.quote(url), shlex.quote(destination)
    # Callers install curl/CA or supply GNU wget. Do not pipe downloads to sh.
    return (
        f"if command -v curl >/dev/null 2>&1; then curl -fL --retry 2 --max-time 120 {url} -o {destination}; "
        f"else wget --tries=3 --timeout=120 -O {destination} {url}; fi && "
        f"printf '%s\\n' {checksum} | sha256sum -c -"
    )


def installation_command(destination: str = "/usr/local/bin/a13n-envd", *, version: str | None = None) -> str:
    """Freeze the same standalone installer used outside managed environments."""
    version = version or VERSION
    path = PurePosixPath(destination)
    if not path.is_absolute():
        raise ValueError("Envd destination must be absolute")
    url = (
        "https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/"
        f"{SOURCE_REVISION}/scripts/install-a13n-envd.sh"
    )
    # Stage alongside the destination so the final rename is atomic, including
    # custom executable names. Installer failure leaves the previous file intact.
    script = (
        'set -eu; destination=$1; parent=${destination%/*}; mkdir -p "$parent"; '
        'root=$(mktemp -d "$parent/.a13n-envd-XXXXXXXX"); '
        "trap 'rm -rf \"$root\"' EXIT HUP INT TERM; "
        'cd "$root"; '
        + download_command(url, "installer.sh", INSTALLER_SHA256)
        + ' || exit 1; test "$(uname -s):$(uname -m)" = Linux:x86_64; '
        'test "$2" = x86_64-unknown-linux-gnu; '
        f'sh installer.sh --version {shlex.quote(version)} --install-dir "$root" --no-add-to-path; '
        'mv -f "$root/a13n-envd" "$destination"'
    )
    return shlex.join(["sh", "-c", script, "a13n-envd-install", destination, TARGET])
