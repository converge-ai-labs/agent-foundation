"""Stable safe Plugin Management failures."""

from __future__ import annotations


class PluginError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def plugin_not_found() -> PluginError:
    return PluginError("plugin_not_found", "The Plugin was not found.", status_code=404)


def plugin_version_not_found() -> PluginError:
    return PluginError("plugin_version_not_found", "The PluginVersion was not found.", status_code=404)


def plugin_artifact_invalid(reason: str) -> PluginError:
    return PluginError(
        "plugin_artifact_invalid",
        "The uploaded Wheel is invalid.",
        status_code=400,
        details={"reason": reason},
    )


def plugin_artifact_limit() -> PluginError:
    return PluginError("plugin_artifact_limit", "The uploaded Wheel exceeds a configured limit.", status_code=400)


def plugin_artifact_unavailable() -> PluginError:
    return PluginError("plugin_artifact_unavailable", "Plugin artifact storage is unavailable.", status_code=503)


def plugin_version_conflict() -> PluginError:
    return PluginError(
        "plugin_version_conflict",
        "This Plugin version already exists with different content.",
        status_code=409,
    )


def plugin_identity_conflict() -> PluginError:
    return PluginError(
        "plugin_identity_conflict",
        "The Wheel conflicts with an existing Plugin identity.",
        status_code=409,
    )


def plugin_state_conflict() -> PluginError:
    return PluginError("plugin_state_conflict", "The Plugin cannot perform this transition.", status_code=409)


def plugin_etag_mismatch() -> PluginError:
    return PluginError("etag_mismatch", "The Plugin representation has changed.", status_code=412)


def plugin_idempotency_conflict() -> PluginError:
    return PluginError(
        "idempotency_conflict",
        "The Idempotency-Key was already used with different Plugin content.",
        status_code=409,
    )


def plugin_runtime_mode_unsupported() -> PluginError:
    return PluginError(
        "plugin_runtime_mode_unsupported",
        "Plugin Runtime commands are unavailable in the configured mode.",
        status_code=409,
    )


def plugin_runtime_control_unavailable() -> PluginError:
    return PluginError(
        "plugin_runtime_control_unavailable",
        "Plugin Runtime command coordination is unavailable.",
        status_code=503,
    )


def plugin_operation_not_found() -> PluginError:
    return PluginError("plugin_operation_not_found", "The Plugin operation was not found.", status_code=404)
