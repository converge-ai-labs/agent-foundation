"""Safe setup projections and explicit environment readiness checks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.models import EnvironmentError
from anyio import fail_after
from pydantic import Field

from a13n_harness_ui.configuration.models import StrictModel
from a13n_harness_ui.configuration.setup import SetupSelection
from a13n_harness_ui.environment_profiles import WINDOWS_EXECUTION_NOTICE, local_sandbox_supported
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.prompts import DEFAULT_SYSTEM_PROMPT
from a13n_harness_ui.sandbox import validate_sandbox_runtime


class SetupProvider(StrictModel):
    provider: Literal["chatgpt", "codex", "grok", "copilot"]
    available: bool
    selected: bool
    action: str
    diagnostic: str | None = None


class SetupStatus(StrictModel):
    needed: bool
    fresh: bool = False
    draft_scope: str = ""
    defaults: SetupSelection = Field(default_factory=lambda: SetupSelection(environment_profile="environment-native"))
    configuration_path: str
    suggested_project_path: str = "."
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
    documentation_url: str = "https://a13n-docs.converge.ai/a13n-envd/"


async def preflight_environment(
    profile_id: Literal["environment-native", "environment-sandbox"],
    project_path: Path,
    *,
    resolve_executable: Callable[[], Awaitable[Path]],
    protected_roots: tuple[Path, ...] = (),
    owned_probe_root: Path | None = None,
) -> EnvironmentReadiness:
    if profile_id == "environment-native":
        return EnvironmentReadiness(
            profile_id=profile_id,
            ready=True,
            code="full_control",
            message="Full Control runs as your Host account with ambient filesystem and network access. It is not a sandbox.",
        )
    if not local_sandbox_supported():
        return EnvironmentReadiness(
            profile_id=profile_id,
            ready=False,
            code="windows_sandbox_unavailable",
            message=WINDOWS_EXECUTION_NOTICE,
            instructions=("Choose Full Control explicitly, or cancel and retain the current selection.",),
        )
    try:
        with fail_after(90):
            executable = await resolve_executable()
            await validate_sandbox_runtime(
                executable, project_path, protected_roots=protected_roots, owned_probe_root=owned_probe_root
            )
        return EnvironmentReadiness(
            profile_id=profile_id,
            ready=True,
            code="sandbox_ready",
            message="Production filesystem, process, and denied-network isolation checks passed.",
        )
    except (HarnessUiError, EnvironmentError, EnvironmentProviderError, OSError, TimeoutError) as exc:
        code = exc.code if isinstance(exc, HarnessUiError) else "sandbox_probe_failed"
        return EnvironmentReadiness(
            profile_id=profile_id,
            ready=False,
            code=code,
            message="Sandbox is not ready. No environment selection or system policy was changed.",
            instructions=(
                "Check the configured a13n-envd release and executable permissions, then Retry.",
                "On Linux, check /usr/bin/bwrap and the distribution's unprivileged user-namespace/AppArmor policy. See the setup documentation; Harness UI will not modify system policy.",
                "Alternatively explicitly choose Full Control (no Sandbox), or Cancel.",
            ),
        )
