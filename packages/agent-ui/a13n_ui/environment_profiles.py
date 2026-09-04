"""Release-owned Agent UI Environment modes and their fixed profile recipes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class EnvironmentMode(StrEnum):
    """Stable user-facing execution modes owned by Agent UI."""

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
    provider_schema_version: str = "1"


FULL_CONTROL_PROFILE_ID: Final = "environment-native"
SANDBOX_PROFILE_ID: Final = "environment-sandbox"

FULL_CONTROL_PROFILE: Final = BuiltInEnvironmentProfile(
    profile_id=FULL_CONTROL_PROFILE_ID,
    mode=EnvironmentMode.full_control,
    name="Full Control",
    description=(
        "Runs commands directly as the Host user; commands may access paths outside Project roots "
        "and use Host networking."
    ),
    provider_key="a13n.direct-local",
    adapter_key="a13n.native-project-root",
)
SANDBOX_PROFILE: Final = BuiltInEnvironmentProfile(
    profile_id=SANDBOX_PROFILE_ID,
    mode=EnvironmentMode.sandbox,
    name="Sandbox",
    description=(
        "Runs commands through local agent-envd with required filesystem and process isolation and denied networking."
    ),
    provider_key="a13n.local-envd",
    adapter_key="a13n.local-envd-project-root",
)
BUILT_IN_ENVIRONMENT_PROFILES: Final = (
    FULL_CONTROL_PROFILE,
    SANDBOX_PROFILE,
)
BUILT_IN_ENVIRONMENT_PROFILES_BY_ID: Final = MappingProxyType(
    {profile.profile_id: profile for profile in BUILT_IN_ENVIRONMENT_PROFILES}
)
BUILT_IN_ENVIRONMENT_PROFILES_BY_MODE: Final = MappingProxyType(
    {profile.mode: profile for profile in BUILT_IN_ENVIRONMENT_PROFILES}
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
