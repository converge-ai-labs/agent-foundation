"""Explicit local Host composition; the production executable is never patched."""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from a13n_service.app import create_app
from a13n_service.configuration.sources import load_settings
from a13n_service.iam import AuthenticatedActor, AuthenticationError, PrincipalRef
from a13n_service.process.components import Components
from a13n_service.settings import ObjectBackend, ProcessRole, Settings
from fastapi import Request

from ..control.fixture_inbox import inbox_router
from .fixture_model import fixture_router

logger = logging.getLogger(__name__)


def settings_for(config: dict, role: str) -> Settings:
    return load_settings(
        overrides={
            "service": {
                "role": role,
                "host": "127.0.0.1",
                "port": urlsplit(config[f"{role}_url"]).port,
                "build_version": "live-test",
            },
            "gateway": {"run_queue_name": "live-test"},
            "pricing": {"auto_update": False},
            "secrets": {"master_key_base64": config["encryption_key"], "encryption_key_id": "live-test-1"},
            "connectivity": {
                "public_origin": config.get("peer_url", config["control_url"]),
                "http_origins": (config["control_url"],),
                "private_endpoint_cidrs": ("127.0.0.1/32",),
                "setup_correlation_secret": config["token"],
                "authorization_callback_urls": ("https://live.example/complete",),
            },
            "models": {"private_endpoint_cidrs": ("127.0.0.1/32",)},
            "filesystem": {"root": Path(config["workspace_root"]).parent / role},
            "worker": config.get("run_faults", {}).get("worker", {}),
        }
    )


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
    settings = load_settings(
        overrides={
            "service": {"role": ProcessRole.control, "host": "127.0.0.1", "port": urlsplit(config["control_url"]).port}
        }
    )
    authenticate = bearer_authenticator(config)
    app = create_app(settings, components=Components(request_authenticator=authenticate))
    app.include_router(
        fixture_router(
            Path(config["workspace_root"]),
            authenticate,
            long_session=config.get("long_session"),
        )
    )
    app.include_router(inbox_router(config, authenticate))
    return settings, app


def local_app(config: dict, role: str):
    if config.get("smoke") and role == "worker":
        from .smoke_host import install

        install()
    if config.get("environment_workers"):
        from ..environment.environment_worker_host import install

        install(config, role)
    if "run_faults" in config:
        from ..run_recovery.run_fault_host import install

        install(config, role)
    if "long_session" in config and role == "worker":
        from ..harness_integration.long_session_host import install_compaction

        install_compaction()
    if config.get("bot_memory") and role == "control" and not config.get("run_faults", {}).get("identity_management"):
        from ..iam.run_fault_identity import install_management_identity

        install_management_identity()
    settings = settings_for(config, role)
    if settings.objects.backend is not ObjectBackend.s3:
        raise RuntimeError(
            "Separate live Control/Worker roles require shared S3 storage. Configure A13N_SERVICE_OBJECT_BACKEND=s3 "
            "and a compatible endpoint/bucket in .env before starting the live roles."
        )
    authenticate = bearer_authenticator(config)
    catalog = None
    if "run_faults" in config:
        from ..iam.run_fault_identity import fault_authenticator

        authenticate = fault_authenticator(config, authenticate)
    from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
    from a13n_harness.providers.environment.catalog import EnvironmentProviderCatalog

    # This isolated Host uses custom identities and explicitly opts into local
    # backends. Production OSS deployments publish these through local_providers.
    builtin_keys = (*settings.environments.provider_builtins, "direct_local", "docker")
    environment_catalog = EnvironmentProviderCatalog(select_builtin_environment_providers(builtin_keys))
    reverse_envd = None
    if config.get("e2b_lifecycle") and role == "worker":
        from ..environment.e2b_host import environment_catalog as e2b_catalog

        environment_catalog = e2b_catalog(config, builtin_keys)
    elif config.get("docker_lifecycle") and role == "worker":
        from ..environment.docker_lifecycle_host import environment_catalog as docker_catalog

        environment_catalog = docker_catalog(config, builtin_keys)
    elif "reverse_envd" in config and role == "worker" and not os.environ.get("LIVE_TEST_NO_REVERSE_ENVD"):
        from ..environment.environment_host import ReverseEnvdHost

        reverse_envd = ReverseEnvdHost(config["reverse_envd"], builtin_keys)
        environment_catalog = reverse_envd.catalog
    elif config.get("websocket_envd") and (role == "control" or os.environ.get("LIVE_TEST_NO_REVERSE_ENVD")):
        environment_catalog = EnvironmentProviderCatalog(
            select_builtin_environment_providers((*builtin_keys, "websocket_envd"))
        )
    if role == "worker":
        from a13n_harness.plugin_factories import build_harness_plugin_factory_catalog

        from ..control.approval_plugin import Factory as ApprovalFactory
        from ..run_recovery.resilience_plugin import Factory as ResilienceFactory

        factories = [ApprovalFactory(), ResilienceFactory()]
        if "run_faults" in config and not config["run_faults"].get("omit_plugin"):
            from ..run_recovery.run_fault_plugin import Factory as RunFaultFactory

            factories.append(RunFaultFactory(config["run_faults"].get("plugin_state_version")))
        catalog = build_harness_plugin_factory_catalog(explicit_factories=tuple(factories))
        logger.info("Live-test Worker installed plugin factories: %s", ", ".join(catalog))
    connector_host = None
    if config.get("local_connectors"):
        from ..harness_integration.connector_host import ConnectorHost

        connector_host = ConnectorHost(config, settings)
    app = create_app(
        settings,
        components=Components(
            request_authenticator=authenticate,
            plugin_factory_catalog=catalog,
            environment_provider_catalog=environment_catalog,
            connector_providers=connector_host.registry if connector_host else None,
        ),
    )
    if config.get("bot_memory") and role in {"control", "worker"}:
        from ..bots.host import BotHost

        BotHost(config).install(app)
    if connector_host is not None:
        connector_host.install(app)
    if reverse_envd is not None:
        reverse_envd.install(app)
    if role == "control":
        if "run_faults" in config:
            from ..iam.run_fault_identity import secret_router
            from ..run_recovery.run_fault_evidence import evidence_router

            app.include_router(evidence_router(config, authenticate))
            app.include_router(secret_router(config, authenticate))
        if "long_session" in config:
            from ..harness_integration.long_session_host import measurements_router

            app.include_router(measurements_router(config, authenticate))
        if config.get("e2b_lifecycle") or config.get("docker_lifecycle") or config.get("environment_workers"):
            from ..environment.lifecycle_host import lifecycle_router

            app.include_router(lifecycle_router(config, authenticate))
        app.include_router(
            fixture_router(
                Path(config["workspace_root"]),
                authenticate,
                long_session=config.get("long_session"),
            )
        )
        app.include_router(inbox_router(config, authenticate))
        from ..harness_integration.fixture_connectivity import connectivity_router
        from ..observability.fixture_telemetry import telemetry_router

        app.include_router(connectivity_router(Path(config["workspace_root"]), config))
        app.include_router(telemetry_router(Path(config["workspace_root"]), authenticate))
    return app
