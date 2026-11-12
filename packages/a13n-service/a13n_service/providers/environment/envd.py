"""Compose administrator-selected daemon settings with provider bootstrap."""

import json

from a13n_harness.environment.envd_settings import EnvdLimits, EnvdSettings


def envd_environment(values: dict[str, str], settings: EnvdSettings) -> dict[str, str]:
    overrides = settings.overrides()
    if not overrides:
        return values
    configuration = json.loads(values.get("A13N_ENVD_CONFIG_JSON", "{}"))
    limits = {**configuration.get("limits", {}), **overrides.pop("limits", {})}
    if limits:
        # Includes older provider-specific size defaults as well as new overrides.
        EnvdLimits.model_validate(limits)
        configuration["limits"] = limits
    configuration.update(overrides)
    return {**values, "A13N_ENVD_CONFIG_JSON": json.dumps(configuration, separators=(",", ":"))}
