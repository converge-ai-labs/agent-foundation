"""Stable Asset Management failures."""

from __future__ import annotations


class AssetManagementError(Exception):
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


def asset_not_found() -> AssetManagementError:
    return AssetManagementError("asset_not_found", "The Asset was not found.", status_code=404)


def asset_content_unavailable() -> AssetManagementError:
    return AssetManagementError(
        "asset_content_unavailable",
        "The Asset content is unavailable.",
        status_code=503,
    )


def asset_limit() -> AssetManagementError:
    return AssetManagementError(
        "asset_limit",
        "The Asset exceeds the configured size limit.",
        status_code=400,
    )


def asset_media_type_invalid() -> AssetManagementError:
    return AssetManagementError(
        "asset_media_type_invalid",
        "The Asset media type is invalid or conflicts with its content.",
        status_code=400,
    )


def asset_content_invalid() -> AssetManagementError:
    return AssetManagementError(
        "asset_content_invalid",
        "The Asset content is invalid.",
        status_code=400,
    )


def asset_idempotency_conflict() -> AssetManagementError:
    return AssetManagementError(
        "asset_idempotency_conflict",
        "The Idempotency-Key was already used with different Asset content or metadata.",
        status_code=409,
    )
