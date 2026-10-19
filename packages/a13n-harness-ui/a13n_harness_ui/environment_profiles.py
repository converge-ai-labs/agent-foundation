"""Release-owned Harness UI Environment modes and their fixed profile recipes."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from pydantic import JsonValue

type LocalProfileId = Literal["environment-native", "environment-envd", "environment-desktop", "environment-sandbox"]


class EnvironmentMode(StrEnum):
    """Stable user-facing execution modes owned by Harness UI."""

    full_control = "full-control"
    sandbox = "sandbox"


@dataclass(frozen=True, slots=True)
class BuiltInEnvironmentProfile:
    """One release-owned Environment profile with fixed execution behavior."""

    profile_id: str
    mode: EnvironmentMode
    name: str
    description: str
    provider_key: str
    adapter_key: str

    @property
    def provider_configuration(self) -> dict[str, JsonValue]:
        if self.profile_id in {"environment-envd", "environment-desktop"}:
            return {
                "launch": {
                    "sandbox": {"mode": "disabled"},
                    "egress": {"mode": "inherit"},
                    "full_control": True,
                    "computer_use": self.profile_id == "environment-desktop",
                    "monitoring": {"sample_interval_ms": 60_000},
                }
            }
        return {}


FULL_CONTROL_PROFILE_ID: Final = "environment-native"
SANDBOX_PROFILE_ID: Final = "environment-sandbox"
WINDOWS_EXECUTION_NOTICE: Final = (
    "Local Sandbox requires Linux, macOS, or Windows with native MXC process isolation. "
    "Readiness is verified by creating and closing an isolation boundary and running production probes. "
    "If unavailable, choose Full Control explicitly; no automatic fallback."
)


def local_sandbox_supported() -> bool:
    """Report implemented platforms; production preflight determines readiness."""
    return sys.platform in {"linux", "darwin", "win32"}


def require_supported_local_profile(profile_id: str | None) -> None:
    """Reject an unavailable built-in profile without altering saved selections."""
    if profile_id == SANDBOX_PROFILE_ID and not local_sandbox_supported():
        raise ValueError(WINDOWS_EXECUTION_NOTICE)


FULL_CONTROL_PROFILE: Final = BuiltInEnvironmentProfile(
    profile_id=FULL_CONTROL_PROFILE_ID,
    mode=EnvironmentMode.full_control,
    name="Full Control",
    description=(
        "Runs commands directly as the Host user; commands may access paths outside Project roots "
        "and use Host networking."
    ),
    provider_key="direct_local",
    adapter_key="a13n.native-project-root",
)
SANDBOX_PROFILE: Final = BuiltInEnvironmentProfile(
    profile_id=SANDBOX_PROFILE_ID,
    mode=EnvironmentMode.sandbox,
    name="Sandbox",
    description=(
        "Runs through local a13n-envd with explicit write grants and inherited Host networking, including localhost. "
        "Windows permits broad disk reads; Linux and macOS restrict reads to granted and runtime paths."
    ),
    provider_key="local_envd",
    adapter_key="a13n.local-envd-project-root",
)
ENVD_PROFILE: Final = BuiltInEnvironmentProfile(
    profile_id="environment-envd",
    mode=EnvironmentMode.full_control,
    name="Full Control · envd",
    description="Runs through local envd as the Host account, without a sandbox. Computer Use is off.",
    provider_key="local_envd",
    adapter_key="a13n.local-envd-project-root",
)
DESKTOP_PROFILE: Final = BuiltInEnvironmentProfile(
    profile_id="environment-desktop",
    mode=EnvironmentMode.full_control,
    name="Full Control · envd + Computer Use",
    description="Local envd with access to the Harness Host desktop, including screen observation and input.",
    provider_key="local_envd",
    adapter_key="a13n.local-envd-project-root",
)
BUILT_IN_ENVIRONMENT_PROFILES: Final = (
    FULL_CONTROL_PROFILE,
    ENVD_PROFILE,
    DESKTOP_PROFILE,
    SANDBOX_PROFILE,
)
BUILT_IN_ENVIRONMENT_PROFILES_BY_ID: Final = MappingProxyType(
    {profile.profile_id: profile for profile in BUILT_IN_ENVIRONMENT_PROFILES}
)
BUILT_IN_ENVIRONMENT_PROFILES_BY_MODE: Final = MappingProxyType(
    {profile.mode: profile for profile in (FULL_CONTROL_PROFILE, SANDBOX_PROFILE)}
)


def built_in_environment_profile(profile_id: str) -> BuiltInEnvironmentProfile | None:
    """Return a release-owned profile recipe by stable profile ID."""

    return BUILT_IN_ENVIRONMENT_PROFILES_BY_ID.get(profile_id)


def environment_profile_id_for_mode(mode: EnvironmentMode | str) -> str:
    """Resolve one user-facing mode to its stable stored profile ID."""

    return BUILT_IN_ENVIRONMENT_PROFILES_BY_MODE[EnvironmentMode(mode)].profile_id


__all__ = [
    "BUILT_IN_ENVIRONMENT_PROFILES",
    "BUILT_IN_ENVIRONMENT_PROFILES_BY_ID",
    "FULL_CONTROL_PROFILE",
    "FULL_CONTROL_PROFILE_ID",
    "SANDBOX_PROFILE",
    "SANDBOX_PROFILE_ID",
    "BuiltInEnvironmentProfile",
    "EnvironmentMode",
    "built_in_environment_profile",
    "environment_profile_id_for_mode",
]
