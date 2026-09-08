"""Explicit local Host composition; the production executable is never patched."""

from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import urlsplit

from a13n_service.app import create_app
from a13n_service.iam import AuthenticatedActor, AuthenticationError, PrincipalRef
from a13n_service.process.components import Components
from a13n_service.settings import ObjectBackend, ProcessRole, Settings
from fastapi import Request

from .fixture_model import fixture_router


def settings_for(config: dict, role: str) -> Settings:
    settings = Settings()
    overrides = {
        "role": ProcessRole(role),
        "host": "127.0.0.1",
        "port": urlsplit(config[f"{role}_url"]).port,
        "build_version": "live-test",
        "gateway_run_queue_name": "live-test",
        "pricing_auto_update": False,
        "secret_master_key_base64": config["encryption_key"],
        "secret_encryption_key_id": "live-test-1",
        "connectivity_public_origin": config["control_url"],
        "connectivity_http_origins": (config["control_url"],),
        "model_private_endpoint_cidrs": ("127.0.0.1/32",),
        "filesystem_root": Path(config["workspace_root"]).parent / role,
    }
    # Validate overrides normally: model_copy would leave SecretStr/path fields untyped.
    return Settings(**{**settings.model_dump(), **overrides})


def bearer_authenticator(config: dict):
    expected = ("Bearer " + config["token"]).encode("utf-8")
    if not config["token"]:
        raise ValueError("Live-test bearer token must not be empty")
    actor = AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=config["user_id"]),
        auth_method="session",
        credential_id="ses_live_test",
        boundary_workspace_id=config["workspace_id"],
    )

    async def authenticate(request: Request) -> AuthenticatedActor:
        values = request.headers.getlist("authorization")
        if len(values) != 1 or not secrets.compare_digest(values[0].encode("utf-8"), expected):
            raise AuthenticationError("Live-test bearer token required")
        return actor

    return authenticate


def authenticated_control(config: dict):
    """Add local authentication and model fixtures to ordinary Control settings."""
    settings = Settings(role=ProcessRole.control, host="127.0.0.1", port=urlsplit(config["control_url"]).port)
    authenticate = bearer_authenticator(config)
    app = create_app(settings, components=Components(request_authenticator=authenticate))
    app.include_router(fixture_router(Path(config["workspace_root"]), authenticate))
    return settings, app


def local_app(config: dict, role: str):
    settings = settings_for(config, role)
    if settings.object_backend is not ObjectBackend.s3:
        raise RuntimeError(
            "Separate live Control/Worker roles require shared S3 storage. Configure A13N_SERVICE_OBJECT_BACKEND=s3 "
            "and a compatible endpoint/bucket in .env before starting the live roles."
        )
    authenticate = bearer_authenticator(config)

    app = create_app(settings, components=Components(request_authenticator=authenticate))
    if role == "control":
        app.include_router(fixture_router(Path(config["workspace_root"]), authenticate))
    return app
