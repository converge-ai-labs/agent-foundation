"""Stable safe Plugin Management failures."""

from __future__ import annotations

from a13n_service.application_errors import ApplicationError, ErrorCategory


class PluginError(ApplicationError):
    pass


def plugin_not_found() -> PluginError:
    return PluginError("plugin_not_found", "The Plugin was not found.", category=ErrorCategory.not_found)


def plugin_version_not_found() -> PluginError:
    return PluginError("plugin_version_not_found", "The PluginVersion was not found.", category=ErrorCategory.not_found)


def plugin_artifact_invalid(reason: str) -> PluginError:
    return PluginError(
        "plugin_artifact_invalid",
        "The uploaded Wheel is invalid.",
        category=ErrorCategory.invalid_request,
        details={"reason": reason},
    )


def plugin_artifact_limit() -> PluginError:
    return PluginError(
        "plugin_artifact_limit",
        "The uploaded Wheel exceeds a configured limit.",
        category=ErrorCategory.invalid_request,
    )


def plugin_artifact_unavailable() -> PluginError:
    return PluginError(
        "plugin_artifact_unavailable", "Plugin artifact storage is unavailable.", category=ErrorCategory.unavailable
    )


def plugin_version_conflict() -> PluginError:
    return PluginError(
        "plugin_version_conflict",
        "This Plugin version already exists with different content.",
        category=ErrorCategory.conflict,
    )


def plugin_identity_conflict() -> PluginError:
    return PluginError(
        "plugin_identity_conflict",
        "The Wheel conflicts with an existing Plugin identity.",
        category=ErrorCategory.conflict,
    )


def plugin_state_conflict() -> PluginError:
    return PluginError(
        "plugin_state_conflict", "The Plugin cannot perform this transition.", category=ErrorCategory.conflict
    )


def plugin_etag_mismatch() -> PluginError:
    return PluginError("etag_mismatch", "The Plugin representation has changed.", category=ErrorCategory.stale_version)


def plugin_idempotency_conflict() -> PluginError:
    return PluginError(
        "idempotency_conflict",
        "The Idempotency-Key was already used with different Plugin content.",
        category=ErrorCategory.conflict,
    )


def plugin_runtime_mode_unsupported() -> PluginError:
    return PluginError(
        "plugin_runtime_mode_unsupported",
        "Plugin Runtime commands are unavailable in the configured mode.",
        category=ErrorCategory.conflict,
    )


def plugin_runtime_control_unavailable() -> PluginError:
    return PluginError(
        "plugin_runtime_control_unavailable",
        "Plugin Runtime command coordination is unavailable.",
        category=ErrorCategory.unavailable,
    )


def plugin_operation_not_found() -> PluginError:
    return PluginError(
        "plugin_operation_not_found", "The Plugin operation was not found.", category=ErrorCategory.not_found
    )
