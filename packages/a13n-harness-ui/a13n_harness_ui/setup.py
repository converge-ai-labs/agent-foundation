"""Safe setup projections and explicit environment readiness checks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, get_args

from a13n_environment import EnvironmentError, EnvironmentProviderError
from a13n_environment.local_envd import (
    LocalEnvdNetworkMode,
    LocalEnvdProviderConfiguration,
    LocalEnvdWorkspaceConfiguration,
    validate_local_envd_runtime,
)
from a13n_harness.model_affinity import SESSION_AFFINITY_PRESETS, SessionAffinityPreset
from anyio import fail_after
from pydantic import Field, JsonValue

from a13n_harness_ui.configuration.models import StrictModel
from a13n_harness_ui.configuration.setup import SetupSelection
from a13n_harness_ui.environment_profiles import WINDOWS_EXECUTION_NOTICE, local_sandbox_supported
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.model_presets import (
    API_MODEL_SUGGESTIONS,
    API_PROVIDER_BY_ROUTE,
    API_PROVIDERS,
    known_context_window,
    settings_presets,
    validate_base_url,
)
from a13n_harness_ui.prompts import DEFAULT_SYSTEM_PROMPT
from a13n_harness_ui.resource_names import model_name


class SetupProvider(StrictModel):
    provider: Literal["codex", "grok"]
    available: bool
    selected: bool
    action: str
    diagnostic: str | None = None


class SetupModelChoice(StrictModel):
    value: str
    label: str


class SetupApiProvider(StrictModel):
    value: str
    label: str
    base_url: str
    models: tuple[str, ...]
    supports_session_affinity: bool = True


class SetupChoices(StrictModel):
    defaults: SetupSelection
    subscription_models: dict[str, tuple[SetupModelChoice, ...]]
    api_providers: tuple[SetupApiProvider, ...]
    session_affinity_presets: tuple[SessionAffinityPreset, ...] = SESSION_AFFINITY_PRESETS


def setup_choices() -> SetupChoices:
    """Project the release's existing authoring catalogs, without provider I/O."""
    return SetupChoices(
        defaults=SetupSelection(environment_profile="environment-native"),
        subscription_models={
            provider: tuple(
                SetupModelChoice(value=value, label=model_name(naming_provider, value))
                for value in get_args(SetupSelection.model_fields[f"{provider}_model"].annotation)
            )
            for provider, naming_provider in (("codex", "codex"), ("grok", "grok-subscription"))
        },
        api_providers=tuple(
            SetupApiProvider(
                value=provider.route,
                label=provider.label,
                base_url=provider.base_url,
                models=API_MODEL_SUGGESTIONS[provider.route],
                supports_session_affinity=provider.supports_session_affinity,
            )
            for provider in API_PROVIDERS
        ),
    )


class SetupModelOptionsRequest(StrictModel):
    provider: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=512)
    base_url: str = Field(default="", max_length=4096)


class SetupSettingsChoice(StrictModel):
    value: str
    label: str
    description: str
    settings: dict[str, JsonValue]


class SetupModelOptions(StrictModel):
    presets: tuple[SetupSettingsChoice, ...]
    context_window: int
    known_context_window: int | None


def setup_model_options(request: SetupModelOptionsRequest) -> SetupModelOptions:
    provider = API_PROVIDER_BY_ROUTE.get(request.provider)
    if provider is None:
        raise HarnessUiError("Choose a supported API provider.", code="setup_provider_unknown")
    if provider.transport != "xai":
        try:
            validate_base_url(request.base_url)
        except ValueError as exc:
            raise HarnessUiError(str(exc), code="setup_base_url_invalid") from exc
    elif request.base_url:
        raise HarnessUiError("The native xAI SDK uses its default endpoint.", code="setup_base_url_unsupported")
    known = known_context_window(provider.route, request.model_id, request.base_url)
    return SetupModelOptions(
        presets=tuple(
            SetupSettingsChoice(
                value=preset.key, label=preset.label, description=preset.description, settings=preset.settings
            )
            for preset in settings_presets(provider.route, request.model_id)
        ),
        context_window=min(350000, known) if known else 350000,
        known_context_window=known,
    )


class SetupStatus(StrictModel):
    needed: bool
    fresh: bool = False
    draft_scope: str = ""
    choices: SetupChoices = Field(default_factory=setup_choices)
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
    documentation_url: str = "https://agent-foundation-docs.converge.ai/a13n-envd/"


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
