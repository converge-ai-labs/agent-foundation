"""Immutable public guest assets shared by provider preparation profiles."""

import base64
import hashlib
import shlex
from importlib.resources import files as resources
from typing import Literal

from .envd_release import download_command

Profile = Literal["headless", "desktop"]
SOURCE_REVISION = "771c7faf65143bd1595f27a7240806049c450fd8"
BASE_IMAGE = "debian:trixie-slim@sha256:4876fdea29cac1a33a3195f1eb1952feb1fca7dd6dd00be3e2f1879cf20d34d2"
HEADLESS_FILES = (
    (
        "deploy/docker/images/sandbox/entrypoint.sh",
        "/usr/local/bin/a13n-sandbox-entrypoint",
        "130fac205b88a997f214c02e5d7c07ebbe533317b4561560850345d28aacfe33",
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
    # Bundle the startup owner with Service so new assets are usable before a
    # source branch is published. Its exact bytes are frozen in every manifest.
    startup = resources(__package__).joinpath("envd_supervise.sh").read_bytes()
    destination = "/usr/local/bin/a13n-envd-supervise"
    encoded = base64.b64encode(startup).decode()
    digest = hashlib.sha256(startup).hexdigest()
    result.append(
        f"printf %s {encoded} | base64 -d > {destination} && "
        f"printf '%s\\n' '{digest}  {destination}' | sha256sum -c - && chmod 0755 {destination}"
    )
    return result


def startup(program: str) -> str:
    """Use guest supervision when installed, retaining older preinstalled images."""
    supervised = (
        "a13n-sandbox-desktop a13n-envd-supervise a13n-envd"
        if program == "a13n-sandbox-desktop"
        else f"a13n-envd-supervise {shlex.quote(program)}"
    )
    return (
        "if command -v a13n-envd-supervise >/dev/null 2>&1; then "
        f"exec a13n-sandbox-entrypoint {supervised}; "
        f"else exec a13n-sandbox-entrypoint {shlex.quote(program)}; fi"
    )
