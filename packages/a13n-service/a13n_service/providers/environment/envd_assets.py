"""Immutable public guest assets shared by provider preparation profiles."""

import shlex
from typing import Literal

from .envd_release import download_command

Profile = Literal["headless", "desktop"]
SOURCE_REVISION = "3674db454385101566bc4fd50644edf4f3dda03a"
BASE_IMAGE = "debian:trixie-slim@sha256:4876fdea29cac1a33a3195f1eb1952feb1fca7dd6dd00be3e2f1879cf20d34d2"
HEADLESS_FILES = (
    (
        "deploy/docker/images/sandbox/entrypoint.sh",
        "/usr/local/bin/a13n-sandbox-entrypoint",
        "c7de2019e091a38fbb335c1c2c9e29c9218dbf7a49019056cc47fbaf5fd27eab",
    ),
)
DESKTOP_FILES = (
    (
        "deploy/docker/images/sandbox/desktop.py",
        "/usr/local/bin/a13n-sandbox-desktop",
        "455fbe3413af57fe36fea0f4a88a1d5bfbfb6bbc6f044121d46f0ce210e785b9",
    ),
    (
        "deploy/docker/images/sandbox/init-skills.sh",
        "/usr/local/bin/a13n-sandbox-init",
        "e973546b911758e35c629d093d7b0496ce7e5af0342a7a10570f192a43508f47",
    ),
    (
        "packages/a13n-service/a13n_service/providers/environment/desktop_guest.py",
        "/opt/a13n-desktop-access.py",
        "68f4ed23ac4729b57262738a3390e239c79db90a1010ae08bf41ed5a1e81ffb4",
    ),
)


def base_preflight() -> str:
    """Fail before package mutation when an explicitly selected base is incompatible."""
    return (
        "set -eu; "
        'fail() { echo "envd_base_unsupported: $*" >&2; exit 1; }; '
        "[ \"$(id -u)\" = 0 ] || fail 'base must select USER root for managed preparation'; "
        "[ \"$(uname -s):$(uname -m)\" = Linux:x86_64 ] || fail 'GNU/Linux x86_64 is required'; "
        "command -v apt-get >/dev/null && command -v getconf >/dev/null && command -v getent >/dev/null "
        "|| fail 'select Debian 13 or Ubuntu 24.04+ with apt-get and glibc'; "
        "libc=$(getconf GNU_LIBC_VERSION) || fail 'glibc >= 2.39 is required'; "
        "case $libc in 'glibc '*) ;; *) fail 'glibc >= 2.39 is required';; esac; "
        "version=${libc#glibc }; major=${version%%.*}; minor=${version#*.}; minor=${minor%%.*}; "
        '[ "$major" -gt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -ge 39 ]; } '
        "|| fail 'glibc >= 2.39 is required; use Debian 13 or Ubuntu 24.04+'; "
        "if getent passwd sandbox >/dev/null || getent passwd 1000 >/dev/null "
        "|| getent group sandbox >/dev/null || getent group 1000 >/dev/null; then "
        "fail 'reserve the sandbox name and UID/GID 1000 in the base image'; fi"
    )


def files(profile: Profile) -> tuple[tuple[str, str, str], ...]:
    return HEADLESS_FILES + (DESKTOP_FILES if profile == "desktop" else ())


def commands(profile: Profile) -> list[str]:
    result = []
    for source, destination, digest in files(profile):
        url = f"https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/{SOURCE_REVISION}/{source}"
        result.append(download_command(url, destination, digest) + f" && chmod 0755 {shlex.quote(destination)}")
    return result


def startup(program: str, *, mode: Literal["daemon", "supervise"] = "supervise") -> str:
    """Select one restart owner; display/access processes keep their outer owner."""
    command = ["a13n-sandbox-desktop", "a13n-envd", mode] if program == "a13n-sandbox-desktop" else [program, mode]
    return "exec " + shlex.join(["a13n-sandbox-entrypoint", *command])
