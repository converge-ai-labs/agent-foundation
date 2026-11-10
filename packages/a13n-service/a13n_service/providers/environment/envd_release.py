"""Render the pinned shell installer; downloads execute inside the target or build."""

import shlex
from pathlib import PurePosixPath

VERSION = "0.7.0"
TARGET = "x86_64-unknown-linux-gnu"
SOURCE_REVISION = "771c7faf65143bd1595f27a7240806049c450fd8"
INSTALLER_SHA256 = "03dd5e79cea8a7279d67e848f0b66f39b0ad91294ab875f8c7c2d5e819c624bf"


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


def installation_command(destination: str = "/usr/local/bin/a13n-envd") -> str:
    """Freeze the same standalone installer used outside managed environments."""
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
        f'sh installer.sh --version {shlex.quote(VERSION)} --install-dir "$root" --no-add-to-path; '
        'mv -f "$root/a13n-envd" "$destination"'
    )
    return shlex.join(["sh", "-c", script, "a13n-envd-install", destination, TARGET])
