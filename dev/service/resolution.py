"""Canonical resolution of checkout identity, ports, paths, and Service settings."""

from __future__ import annotations

from pathlib import Path

from a13n_service.configuration.sources import load_settings

from .environment import ROOT, Environment
from .instance import ensure_instance, load_instance


def resolve_environment(config: Path, *, create: bool = True, root: Path = ROOT) -> Environment | None:
    instance = ensure_instance(root) if create else load_instance(root)
    if instance is None:
        return None
    ports = instance.ports
    console_origin = f"http://127.0.0.1:{ports.console}"
    model_origin = f"http://127.0.0.1:{ports.model}"
    state = root / "var/dev/service"
    overrides = {
        "service": {
            "host": "127.0.0.1",
            "port": ports.service,
            "instance_id": f"local-{instance.id}",
            "deployment_environment_name": f"local-{instance.id}",
        },
        "database": {
            "url": (
                f"postgresql+psycopg://a13n_service_dev:local-only-password@127.0.0.1:{ports.postgres}/a13n_service_dev"
            )
        },
        "redis": {"url": f"redis://127.0.0.1:{ports.redis}/0"},
        "objects": {"local_root": state / "objects"},
        "filesystem": {"root": state / "files"},
        "iam": {
            "public_origin": console_origin,
            "session_cookie_name": f"a13n_session_{instance.id}",
        },
        "connectivity": {
            "public_origin": console_origin,
            "authorization_callback_urls": [console_origin + "/connections/callback"],
            "http_origins": [console_origin, model_origin],
        },
    }
    return Environment(load_settings(config, overrides=overrides), instance, root.resolve())
