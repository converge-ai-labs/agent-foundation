"""One guest-owned upgrade job; Service selects the version and restart owner."""

import hashlib
import shlex
from collections.abc import Awaitable, Callable

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.environment.errors import provider_error
from packaging.version import Version

from .envd_install import dispatch
from .envd_release import installation_command


def script(version: str, *, restart: list[str] | None = None) -> str:
    Version(version)
    # An external manager is restarted only after installation changed. The
    # marker survives an interrupted installer and a lost restart receipt.
    return "\n".join(
        [
            "set -eu",
            "current=$(a13n-envd --version 2>/dev/null || true)",
            f'if [ "$current" != {shlex.quote("a13n-envd " + version)} ]; then',
            " : > restart-needed",
            " " + installation_command(version=version),
            "fi",
            (
                "if [ -f restart-needed ]; then " + shlex.join(restart) + "; rm -f restart-needed; fi"
                if restart is not None
                else shlex.join(["a13n-envd", "restart", "--runtime-dir", "/run/a13n-envd-state", "--version", version])
            ),
        ]
    )


async def upgrade(
    execute: Callable[[list[str]], Awaitable[str]],
    *,
    provider_key: str,
    version: str,
    operation_id: str,
    restart: list[str] | None = None,
) -> bool:
    content = script(version, restart=restart)
    revision = hashlib.sha256(content.encode()).hexdigest()
    root = "/var/lib/a13n-envd/update-" + version
    status = (await execute(["sh", "-c", dispatch(content, revision, operation_id, root=root)])).strip()
    if status == "ready":
        return True
    if status == "running":
        return False
    raise provider_error(
        provider_key,
        "envd_upgrade_failed",
        Category.INVALID,
        certainty=Certainty.KNOWN,
        description="The managed Envd upgrade did not complete. Inspect the target's protected /var/lib/a13n-envd/update-<version>/output and explicitly retry the lifecycle operation. The previous binary is retained if installation failed; execution commands are never replayed.",
    )
