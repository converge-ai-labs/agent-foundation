"""Stable Docker provider failures shared by factory and adapter modules."""

from ..errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
)

_PROVIDER_KEY = "a13n.docker"
_STATE_VERSION = "1"


def provider_error(
    description: str,
    *,
    code: str,
    category: EnvironmentProviderErrorCategory,
    schema_version: str | None = None,
    certainty: EnvironmentProviderOutcomeCertainty = EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
    recovery_hint: EnvironmentProviderRecoveryHint = EnvironmentProviderRecoveryHint.FIX_INPUT,
) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        description,
        code=code,
        category=category,
        certainty=certainty,
        recovery_hint=recovery_hint,
        context=EnvironmentProviderErrorContext(
            provider_key=_PROVIDER_KEY,
            schema_version=schema_version,
            state_version=_STATE_VERSION if schema_version is None else None,
        ),
    )


def state_failure(description: str) -> EnvironmentProviderError:
    return provider_error(
        description,
        code="provider_state_invalid",
        category=EnvironmentProviderErrorCategory.INVALID,
    )


def conflict_failure(description: str) -> EnvironmentProviderError:
    return provider_error(
        description,
        code="provider_state_conflict",
        category=EnvironmentProviderErrorCategory.CONFLICT,
    )


def target_conflict_failure(description: str) -> EnvironmentProviderError:
    return provider_error(
        description,
        code="provider_target_conflict",
        category=EnvironmentProviderErrorCategory.CONFLICT,
    )


def missing_failure(description: str) -> EnvironmentProviderError:
    return provider_error(
        description,
        code="provider_target_missing",
        category=EnvironmentProviderErrorCategory.MISSING,
        certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
        recovery_hint=EnvironmentProviderRecoveryHint.NONE,
    )


def runtime_failure(description: str) -> EnvironmentProviderError:
    return provider_error(
        description,
        code="provider_unavailable",
        category=EnvironmentProviderErrorCategory.UNAVAILABLE,
        certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
        recovery_hint=EnvironmentProviderRecoveryHint.REFRESH_RUNTIME,
    )


def unknown_failure(description: str) -> EnvironmentProviderError:
    return provider_error(
        description,
        code="provider_unknown_outcome",
        category=EnvironmentProviderErrorCategory.UNKNOWN_OUTCOME,
        certainty=EnvironmentProviderOutcomeCertainty.UNKNOWN,
        recovery_hint=EnvironmentProviderRecoveryHint.RECONCILE,
    )


def cleanup_failure(description: str) -> EnvironmentProviderError:
    return provider_error(
        description,
        code="provider_cleanup_failed",
        category=EnvironmentProviderErrorCategory.CLEANUP,
        certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
        recovery_hint=EnvironmentProviderRecoveryHint.RETRY_SAME_OPERATION,
    )
