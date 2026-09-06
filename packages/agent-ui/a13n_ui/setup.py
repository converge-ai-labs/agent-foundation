"""Safe setup projections and explicit environment readiness checks."""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from a13n_environment_provider import EnvironmentError, EnvironmentProviderError
from a13n_environment_provider.local_envd import (
    LocalEnvdNetworkMode,
    LocalEnvdProviderConfiguration,
    LocalEnvdWorkspaceConfiguration,
    validate_local_envd_runtime,
)
from anyio import fail_after
from pydantic import Field

from a13n_ui.configuration.models import StrictModel
from a13n_ui.errors import AgentUiError
from a13n_ui.prompts import DEFAULT_SYSTEM_PROMPT


class SetupProvider(StrictModel):
    provider: Literal["codex", "grok"]
    available: bool
    selected: bool
    action: str
    diagnostic: str | None = None


class SetupStatus(StrictModel):
    needed: bool
    configuration_path: str
    suggested_project_path: str = "."
    generation: str
    providers: tuple[SetupProvider, ...]
    agents: dict[str, str]
    projects: dict[str, str]
    project_paths: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    default_agent: str | None
    default_project: str | None
    environment_profile: str
    diagnostic: str | None = None


class EnvironmentReadiness(StrictModel):
    profile_id: Literal["environment-native", "environment-sandbox"]
    ready: bool
    code: str
    message: str
    instructions: tuple[str, ...] = ()
    documentation_url: str = "https://agent-foundation-docs.converge.ai/agent-envd/"


async def preflight_environment(
    profile_id: Literal["environment-native", "environment-sandbox"],
    project_path: Path,
    *,
    resolve_executable: Callable[[], Awaitable[Path]],
) -> EnvironmentReadiness:
    if profile_id == "environment-native":
        return EnvironmentReadiness(
            profile_id=profile_id,
            ready=True,
            code="full_control",
            message="Full Control runs as your Host account with ambient filesystem and network access. It is not a sandbox.",
        )
    if sys.platform == "win32":
        return EnvironmentReadiness(
            profile_id=profile_id,
            ready=False,
            code="sandbox_platform_unsupported",
            message="Production Sandbox isolation is not implemented on Windows. You can explicitly choose Full Control instead.",
            instructions=("Choose Full Control to run without Sandbox, or cancel and retain your current selection.",),
        )
    try:
        with fail_after(90):
            executable = await resolve_executable()
            await validate_local_envd_runtime(
                executable,
                LocalEnvdProviderConfiguration(
                    workspace=LocalEnvdWorkspaceConfiguration(path=project_path),
                    execution_network=LocalEnvdNetworkMode.DENY,
                ),
            )
        return EnvironmentReadiness(
            profile_id=profile_id,
            ready=True,
            code="sandbox_ready",
            message="Production filesystem, process, and denied-network isolation checks passed.",
        )
    except (AgentUiError, EnvironmentError, EnvironmentProviderError, OSError, TimeoutError) as exc:
        code = exc.code if isinstance(exc, AgentUiError) else "sandbox_probe_failed"
        return EnvironmentReadiness(
            profile_id=profile_id,
            ready=False,
            code=code,
            message="Sandbox is not ready. No environment selection or system policy was changed.",
            instructions=(
                "Check the configured agent-envd release and executable permissions, then Retry.",
                "On Linux, check /usr/bin/bwrap and the distribution's unprivileged user-namespace/AppArmor policy. See the setup documentation; Agent UI will not modify system policy.",
                "Alternatively explicitly choose Full Control (no Sandbox), or Cancel.",
            ),
        )
