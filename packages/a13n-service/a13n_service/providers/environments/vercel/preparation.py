"""Install on the final persistent Vercel target; rejoin its original guest job."""

import base64
import hashlib
import json
import re
import shlex

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.vercel.commands import command
from a13n_harness.providers.environment.vercel.shared import SandboxResponse, VercelReference

from a13n_service.providers.environment import envd_assets
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
        "&& groupadd --gid 1001 sandbox && useradd --uid 1001 --gid sandbox --create-home --shell /bin/bash sandbox "
        "&& install -d -o sandbox -g sandbox /workspace "
        "&& install -d -m 0700 /var/lib/a13n-envd /run/a13n-envd-state "
        "&& rm -rf /var/lib/apt/lists/*",
        installation_command(),
        *envd_assets.commands("headless"),
    ]
    return {"image": "vercel/sandbox/ubuntu", "script": "set -eu\n" + "\n".join(commands)}


def revision(*, desktop: bool) -> str:
    return hashlib.sha256(json.dumps(specification(desktop=desktop), sort_keys=True).encode()).hexdigest()


def dispatch_script(manifest: dict[str, str], expected: str, *, root: str = ROOT) -> str:
    """Keep the first script across deployments; never rerun a failed partial install."""
    current = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    launcher = """set -eu
exec 9>install.lock
flock -n 9 || exit 0
status=$(cat status 2>/dev/null || true)
case "$status" in
 ready|failed) exit 0 ;;
 running) printf failed > status; exit 0 ;;
esac
printf running > status
trap 'printf failed > status' EXIT
# Bound the owned guest job independently of Service request/worker lifetimes.
timeout 1500 sh install.sh
printf ready > status
trap - EXIT
"""
    install = base64.b64encode(manifest["script"].encode()).decode()
    launch = base64.b64encode(launcher.encode()).decode()
    return f"""set -eu
umask 077
install -d -m 0700 {shlex.quote(root)}
cd {shlex.quote(root)}
exec 8>dispatch.lock
flock -n 8 || {{ printf running; exit 0; }}
if [ -f revision ]; then
 [ "$(cat revision)" = {shlex.quote(expected)} ] || {{ printf conflict; exit 0; }}
else
 [ {shlex.quote(expected)} = {shlex.quote(current)} ] || {{ printf incompatible; exit 0; }}
 printf %s {install} | base64 -d > install.sh
 printf %s {launch} | base64 -d > launch.sh
 printf %s {shlex.quote(expected)} > revision
fi
status=$(cat status 2>/dev/null || true)
case "$status" in
 ready|failed) printf %s "$status" ;;
 *) nohup sh launch.sh > output 2>&1 < /dev/null 8>&- &
    printf running ;;
esac
"""


async def prepare(reference: VercelReference, response: SandboxResponse) -> bool:
    expected = response.sandbox.tags.get(TAG, "")
    if re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise failure("vercel", "envd_preparation_revision_missing", Category.CONFLICT)
    output = await command(
        reference,
        response.session.id,
        ["sh", "-c", dispatch_script(specification(desktop=reference.config.desktop), expected)],
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
