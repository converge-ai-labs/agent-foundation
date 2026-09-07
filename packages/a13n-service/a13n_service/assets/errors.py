"""Stable Asset Management failures."""

from __future__ import annotations

from a13n_service.application_errors import ApplicationError, ErrorCategory


class AssetError(ApplicationError):
    pass


def asset_not_found() -> AssetError:
    return AssetError("asset_not_found", "The Asset was not found.", category=ErrorCategory.not_found)


def asset_content_unavailable() -> AssetError:
    return AssetError(
        "asset_content_unavailable",
        "The Asset content is unavailable.",
        category=ErrorCategory.unavailable,
    )


def asset_limit() -> AssetError:
    return AssetError(
        "asset_limit",
        "The Asset exceeds the configured size limit.",
        category=ErrorCategory.invalid_request,
    )


def asset_media_type_invalid() -> AssetError:
    return AssetError(
        "asset_media_type_invalid",
        "The Asset media type is invalid or conflicts with its content.",
        category=ErrorCategory.invalid_request,
    )


def asset_content_invalid() -> AssetError:
    return AssetError(
        "asset_content_invalid",
        "The Asset content is invalid.",
        category=ErrorCategory.invalid_request,
    )


def asset_idempotency_conflict() -> AssetError:
    return AssetError(
        "asset_idempotency_conflict",
        "The Idempotency-Key was already used with different Asset content or metadata.",
        category=ErrorCategory.conflict,
    )
