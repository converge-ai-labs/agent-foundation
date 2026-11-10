"""Prepare the selected E2B sandbox, alongside E2B's own SDK daemon."""

from __future__ import annotations

import hashlib
import shlex
from typing import TYPE_CHECKING

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import provider_error
from a13n_harness.providers.environment.e2b.errors import sdk_errors

from a13n_service.providers.environment import envd_assets
from a13n_service.providers.environment.envd_install import dispatch
from a13n_service.providers.environment.envd_release import installation_command

if TYPE_CHECKING:
    from e2b import AsyncSandbox

ROOT = "/var/lib/a13n-e2b-install"
TAG = "a13n_preparation"

# 0.7.0's published GNU binary requires glibc 2.39. Never upgrade a stock
# template's libc or overwrite E2B's own SDK envd to make it fit.
PREREQUISITES = """set -eu
[ "$(uname -s):$(uname -m)" = Linux:x86_64 ] || exit 1
libc=$(getconf GNU_LIBC_VERSION) || exit 1
version=${libc#glibc }
[ "$version" != "$libc" ] || exit 1
[ "$(printf '%s\\n' 2.39 "$version" | sort -V | head -n 1)" = 2.39 ] || exit 1
for command in apt-get flock timeout base64 nohup; do command -v "$command" >/dev/null || exit 1; done
"""


def installation_script() -> str:
    return "set -eu\n" + "\n".join(
        [
            "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
            "ca-certificates curl libgcc-s1 util-linux sudo "
            "&& (getent group sandbox >/dev/null || groupadd --gid 1001 sandbox) "
            "&& (id sandbox >/dev/null 2>&1 || useradd --uid 1001 --gid sandbox --create-home --shell /bin/bash sandbox) "
            '&& test "$(id -u sandbox)" = 1001 && test "$(id -g sandbox)" = 1001 '
            "&& install -d -o sandbox -g sandbox /workspace "
            "&& install -d -m 0700 /var/lib/a13n-envd /run/a13n-envd-state "
            "&& rm -rf /var/lib/apt/lists/*",
            installation_command(),
            *envd_assets.commands("headless"),
        ]
    )


def revision() -> str:
    return hashlib.sha256(installation_script().encode()).hexdigest()


async def install(sandbox: AsyncSandbox, expected: str, operation_id: str) -> bool:
    from e2b.sandbox.commands.command_handle import CommandExitException

    with sdk_errors():
        try:
            await sandbox.commands.run(
                shlex.join(["sh", "-c", PREREQUISITES]), user="root", timeout=10, request_timeout=15
            )
        except CommandExitException:
            raise provider_error(
                "e2b",
                "envd_userspace_unsupported",
                Category.UNSUPPORTED,
                description="Headless preparation requires GNU/Linux x86_64, glibc >= 2.39, apt-get and util-linux. Select a Debian 13 or Ubuntu 24.04+ E2B template and preparation=headless, or supply a compatible preinstalled template. The stock Bookworm base is incompatible with pinned Envd 0.7.0; its libc is never upgraded automatically.",
            ) from None
    with sdk_errors(mutation=True):
        result = await sandbox.commands.run(
            shlex.join(["sh", "-c", dispatch(installation_script(), expected, operation_id, root=ROOT)]),
            user="root",
            timeout=10,
            request_timeout=15,
        )
    status = result.stdout.strip()
    if status == "ready":
        return True
    if status == "running":
        return False
    raise provider_error(
        "e2b",
        "envd_preparation_failed" if status == "failed" else "envd_preparation_revision_conflict",
        Category.INVALID,
        description="Envd preparation did not complete. Inspect the owned sandbox's protected /var/lib/a13n-e2b-install/output; explicitly retry the failed creation or delete the target.",
    )
