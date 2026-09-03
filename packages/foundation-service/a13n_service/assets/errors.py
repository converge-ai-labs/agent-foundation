"""Stable Asset Management failures."""

from __future__ import annotations

from a13n_service.public_errors import PublicError


class AssetError(PublicError):
    pass


def asset_not_found() -> AssetError:
    return AssetError("asset_not_found", "The Asset was not found.", status_code=404)


def asset_content_unavailable() -> AssetError:
    return AssetError(
        "asset_content_unavailable",
        "The Asset content is unavailable.",
        status_code=503,
    )


def asset_limit() -> AssetError:
    return AssetError(
        "asset_limit",
        "The Asset exceeds the configured size limit.",
        status_code=400,
    )


def asset_media_type_invalid() -> AssetError:
    return AssetError(
        "asset_media_type_invalid",
        "The Asset media type is invalid or conflicts with its content.",
        status_code=400,
    )


def asset_content_invalid() -> AssetError:
    return AssetError(
        "asset_content_invalid",
        "The Asset content is invalid.",
        status_code=400,
    )


def asset_idempotency_conflict() -> AssetError:
    return AssetError(
        "asset_idempotency_conflict",
        "The Idempotency-Key was already used with different Asset content or metadata.",
        status_code=409,
    )
