"""Run the stock Console and Service against a disposable Dex identity provider."""

import argparse
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import httpx2
import uvicorn
import yaml
from a13n_logging import LogFormat, configure_logging
from a13n_service.app import build_app
from a13n_service.distribution import OSS
from a13n_service.migrations.runner import upgrade
from a13n_service.settings import Database, load_settings
from pydantic import SecretStr
from testcontainers.core.container import DockerContainer
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from dev.service.console_review import ROOT, available_port, service_config

CONSOLE = ROOT / "frontend/apps/a13n-console"
EMAIL = "admin@example.com"
LOCAL_PASSWORD = "local-fixture-password"
DEX_PASSWORD = "dex-fixture-password"
DEX_CLIENTS = {"team": "Team Identity", "engineering": "Engineering SSO"}
BRAND_PREVIEW = {
    "google": ("Google", "https://accounts.google.com"),
    "microsoft": ("Microsoft", "https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0"),
}
LONG_LABEL = "Research and Engineering Workspace Identity for Regional Offices"


def wait_for(url: str, process: subprocess.Popen | None = None) -> None:
    deadline = time.monotonic() + 40
    with httpx2.Client(timeout=1, trust_env=False) as client:
        while time.monotonic() < deadline and (process is None or process.poll() is None):
            try:
                if client.get(url).is_success:
                    return
            except httpx2.HTTPError:
                pass
            time.sleep(0.15)
    raise RuntimeError(f"Fixture did not become ready at {url}")


@contextmanager
def service(directory: Path, config: Path, port: int, *, password_enabled: bool | None = None):
    environment = {name: value for name, value in os.environ.items() if not name.startswith(("A13N_", "OTEL_"))}
    if password_enabled is not None:
        environment["A13N_AUTH__PASSWORD_ENABLED"] = str(password_enabled).lower()
    with (directory / "service.log").open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "dev.service.oidc_review", "--serve", str(config), "--port", str(port)],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            wait_for(f"http://127.0.0.1:{port}/readyz", process)
            yield
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def write_dex(directory: Path, issuer: str, origin: str) -> Path:
    config = directory / "dex.yaml"
    # Bcrypt hash of the public fixture password declared above.
    password_hash = "$2b$12$tYO6do5QWN4WxLDEimgI0ee9XVsC.MPr9dTC/ot8KTp5PfuiowZ.G"

    config.write_text(
        yaml.safe_dump(
            {
                "issuer": issuer,
                "storage": {"type": "memory"},
                "web": {"http": "0.0.0.0:5556"},
                "oauth2": {
                    "skipApprovalScreen": True,
                    "pkce": {"enforce": True, "codeChallengeMethodsSupported": ["S256"]},
                },
                "enablePasswordDB": True,
                "staticPasswords": [
                    {
                        "email": EMAIL,
                        "hash": password_hash,
                        "username": "Alex Rivera",
                        "userID": "fixture-operator",
                        "groups": ["console-admins"],
                    }
                ],
                "staticClients": [
                    {
                        "id": name,
                        "name": label,
                        "secret": "public-fixture-client-secret",
                        "redirectURIs": [f"{origin}/api/v1/auth/oidc/{name}/callback"],
                    }
                    for name, label in DEX_CLIENTS.items()
                ],
            }
        )
    )
    return config


def oidc_settings(issuer: str, workspace_id: str, phase: str) -> str:
    password_enabled = phase != "recovery"  # Exercise env precedence in both directions.
    result = f"\n[auth]\npassword_enabled = {str(password_enabled).lower()}\n[auth.oidc]\ndefault_workspace_id = {json.dumps(workspace_id)}\nsession_seconds = 900\n"
    providers = {name: (label, issuer) for name, label in DEX_CLIENTS.items()}
    if phase == "mixed":
        # Brand rendering only: these issuers are not contacted in the browser flow.
        providers = BRAND_PREVIEW | providers
    elif phase == "single":
        providers = {"team": (LONG_LABEL, issuer)}
    for name, (label, provider_issuer) in providers.items():
        result += (
            f"[auth.oidc.providers.{name}]\nlabel = {json.dumps(label)}\nissuer = {json.dumps(provider_issuer)}\n"
            f"client_id = {json.dumps(name)}\nclient_secret = 'public-fixture-client-secret'\n"
        )
        if name == "team" and phase in {"mapped", "oidc-only", "single"}:
            group = "other-group" if phase == "single" else "console-admins"
            result += f"extra_scopes = ['groups']\norg_admin = {{claim = 'groups', value = '{group}'}}\n"

    return result


def run(directory: Path, *, manual: bool) -> None:
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    if not (CONSOLE / "dist/index.html").exists():
        raise ValueError("Build Console first: make a13n-console-build")
    port, dex_port = available_port(), available_port()
    origin, issuer = f"http://localhost:{port}", f"http://127.0.0.1:{dex_port}/dex"
    config = directory / "service.toml"
    dex = write_dex(directory, issuer, origin)
    # The Docker SDK accepts (address, port); testcontainers' annotation only mentions shorthand ports.
    idp = (
        DockerContainer("ghcr.io/dexidp/dex:v2.46.1")
        .with_bind_ports(5556, cast(str, ("127.0.0.1", dex_port)))
        .with_volume_mapping(dex, "/etc/dex/config.yaml")
        .with_command("dex serve /etc/dex/config.yaml")
    )
    with (
        PostgresContainer("postgres:17-alpine", driver="psycopg") as postgres,
        RedisContainer("redis:7-alpine") as redis,
        idp,
    ):
        wait_for(issuer + "/.well-known/openid-configuration")
        database_url = postgres.get_connection_url()
        redis_url = f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"
        upgrade(Database(url=SecretStr(database_url)), OSS)
        base = service_config(directory, database_url, redis_url, console=origin, workspace_id="", origins=())
        config.write_text(base)
        with service(directory, config, port), httpx2.Client(base_url=origin, trust_env=False) as client:
            output = client.post("/api/v1/auth/bootstrap", json={"email": EMAIL, "password": LOCAL_PASSWORD})
            output.raise_for_status()
            org = client.get("/api/v1/organizations").raise_for_status().json()["items"][0]["id"]
            workspace = (
                client.get(f"/api/v1/organizations/{org}/workspaces").raise_for_status().json()["items"][0]["id"]
            )
        fixture = {
            "origin": origin,
            "issuer": issuer,
            "organization": org,
            "workspace": workspace,
            "email": EMAIL,
            "localPassword": LOCAL_PASSWORD,
            "dexPassword": DEX_PASSWORD,
            "longLabel": LONG_LABEL,
        }
        (directory / "fixture.json").write_text(json.dumps(fixture))
        for phase in ("mixed", "mapped", "oidc-only", "single", "recovery"):
            config.write_text(base + oidc_settings(issuer, workspace, phase))
            override = None if phase in {"mixed", "mapped"} else phase == "recovery"
            with service(directory, config, port, password_enabled=override):
                if manual:
                    print(
                        f"Console: {origin}/login\nDex: {EMAIL} / {DEX_PASSWORD}\nPassword: {EMAIL} / {LOCAL_PASSWORD}",
                        flush=True,
                    )
                    try:
                        while True:
                            time.sleep(1)
                    except KeyboardInterrupt:
                        return
                subprocess.run(
                    ["node", "tests/oidc-browser.mjs", str(directory), phase], cwd=CONSOLE, check=True, timeout=120
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, help="New directory for fixture configuration, logs and screenshots")
    parser.add_argument("--manual", action="store_true", help="Keep the mixed-mode fixture open for manual inspection")
    parser.add_argument("--serve", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        configure_logging(log_format=LogFormat.json, logger_names=("a13n_service",))
        app = build_app(settings=load_settings(args.serve), apps=(), console=CONSOLE / "dist")
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
    elif args.directory:
        run(args.directory.resolve(), manual=args.manual)
    else:
        parser.error("--directory is required")


if __name__ == "__main__":
    main()
