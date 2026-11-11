"""Install on the final persistent Vercel target; rejoin its original guest job."""

import hashlib
import re

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.vercel.commands import command
from a13n_harness.providers.environment.vercel.shared import SandboxResponse, VercelReference

from a13n_service.providers.environment import envd_assets
from a13n_service.providers.environment.envd_install import dispatch
from a13n_service.providers.environment.envd_release import installation_command

from .desktop_build import specification as desktop_specification

TAG = "a13n_preparation"
ROOT = "/var/lib/a13n-preparation"


def specification(*, desktop: bool) -> dict[str, str]:
    if desktop:
        value = desktop_specification()
        return {"image": str(value["image"]), "script": str(value["script"])}
    commands = [
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
    return {"image": "vercel/sandbox/ubuntu", "script": "set -eu\n" + "\n".join(commands)}


def revision(*, desktop: bool) -> str:
    return hashlib.sha256(specification(desktop=desktop)["script"].encode()).hexdigest()


def dispatch_script(manifest: dict[str, str], expected: str, operation_id: str, *, root: str = ROOT) -> str:
    """Rejoin the frozen job; a new lifecycle operation may retry a confirmed failure."""
    return dispatch(manifest["script"], expected, operation_id, root=root, timeout_seconds=1500)


async def prepare(reference: VercelReference, response: SandboxResponse, operation_id: str) -> bool:
    expected = response.sandbox.tags.get(TAG, "")
    if re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise failure("vercel", "envd_preparation_revision_missing", Category.CONFLICT)
    output = await command(
        reference,
        response.session.id,
        ["sh", "-c", dispatch_script(specification(desktop=reference.config.desktop), expected, operation_id)],
        sudo=True,
        timeout=10,
        max_output=1024,
    )
    status = output.strip()
    if status == "ready":
        return True
    if status == "running":
        return False
    raise failure(
        "vercel",
        "envd_preparation_failed" if status == "failed" else "envd_preparation_revision_conflict",
        Category.INVALID,
    )
