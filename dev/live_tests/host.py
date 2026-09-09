"""Explicit local Host composition; the production executable is never patched."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from a13n_service.app import create_app
from a13n_service.iam import AuthenticatedActor, AuthenticationError, PrincipalRef
from a13n_service.process.components import Components
from a13n_service.settings import ObjectBackend, ProcessRole, Settings
from fastapi import Request

from .fixture_inbox import inbox_router
from .fixture_model import fixture_router

logger = logging.getLogger(__name__)


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
        "connectivity_private_endpoint_cidrs": ("127.0.0.1/32",),
        "connectivity_setup_correlation_secret": config["token"],
        "model_private_endpoint_cidrs": ("127.0.0.1/32",),
        "filesystem_root": Path(config["workspace_root"]).parent / role,
    }
    # Validate overrides normally: model_copy would leave SecretStr/path fields untyped.
    return Settings(**{**settings.model_dump(), **overrides})


def bearer_authenticator(config: dict):
    identities = [config, *([config["other_identity"]] if "other_identity" in config else [])]
    if any(not identity["token"] for identity in identities):
        raise ValueError("Live-test bearer token must not be empty")
    if len({identity["token"] for identity in identities}) != len(identities):
        raise ValueError("Live-test identities must have distinct bearer tokens")

    async def authenticate(request: Request) -> AuthenticatedActor:
        values = request.headers.getlist("authorization")
        if len(values) == 1:
            for identity in identities:
                if secrets.compare_digest(values[0].encode("utf-8"), ("Bearer " + identity["token"]).encode("utf-8")):
                    return AuthenticatedActor(
                        principal=PrincipalRef(principal_type="user", principal_id=identity["user_id"]),
                        auth_method="session",
                        credential_id="ses_live_test",
                        boundary_workspace_id=identity["workspace_id"],
                    )
        raise AuthenticationError("Live-test bearer token required")

    return authenticate


def authenticated_control(config: dict):
    """Add local authentication and model fixtures to ordinary Control settings."""
    settings = Settings(role=ProcessRole.control, host="127.0.0.1", port=urlsplit(config["control_url"]).port)
    authenticate = bearer_authenticator(config)
    app = create_app(settings, components=Components(request_authenticator=authenticate))
    app.include_router(fixture_router(Path(config["workspace_root"]), authenticate))
    app.include_router(inbox_router(config, authenticate))
    return settings, app


def local_app(config: dict, role: str):
    settings = settings_for(config, role)
    if settings.object_backend is not ObjectBackend.s3:
        raise RuntimeError(
            "Separate live Control/Worker roles require shared S3 storage. Configure A13N_SERVICE_OBJECT_BACKEND=s3 "
            "and a compatible endpoint/bucket in .env before starting the live roles."
        )
    authenticate = bearer_authenticator(config)
    catalog = None
    environment_catalog = None
    reverse_envd = None
    if "reverse_envd" in config and role == "worker":
        from .environment_host import ReverseEnvdHost

        reverse_envd = ReverseEnvdHost(config["reverse_envd"], settings.environment_provider_builtins)
        environment_catalog = reverse_envd.catalog
    elif role == "control" and config.get("websocket_envd"):
        from a13n_environment import build_environment_provider_catalog

        environment_catalog = build_environment_provider_catalog(
            builtin_keys=tuple(dict.fromkeys((*settings.environment_provider_builtins, "a13n.websocket-envd")))
        )
    if role == "worker":
        from a13n_harness.plugin_factories import build_harness_plugin_factory_catalog

        from .approval_plugin import Factory as ApprovalFactory
        from .resilience_plugin import Factory as ResilienceFactory

        catalog = build_harness_plugin_factory_catalog(explicit_factories=(ApprovalFactory(), ResilienceFactory()))
        logger.info("Live-test Worker installed plugin factories: %s", ", ".join(catalog))
    app = create_app(
        settings,
        components=Components(
            request_authenticator=authenticate,
            plugin_factory_catalog=catalog,
            environment_provider_catalog=environment_catalog,
        ),
    )
    if reverse_envd is not None:
        reverse_envd.install(app)
    if role == "control":
        app.include_router(fixture_router(Path(config["workspace_root"]), authenticate))
        app.include_router(inbox_router(config, authenticate))
        from .fixture_connectivity import connectivity_router
        from .fixture_telemetry import telemetry_router

        app.include_router(connectivity_router(Path(config["workspace_root"]), config))
        app.include_router(telemetry_router(Path(config["workspace_root"]), authenticate))
    return app
