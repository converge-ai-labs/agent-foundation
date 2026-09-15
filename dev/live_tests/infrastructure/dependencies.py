"""Explicit infrastructure ownership for disposable live-test labs."""

from __future__ import annotations

import os
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import anyio
import psycopg
from psycopg import sql
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator
from sqlalchemy.engine import make_url

MODE_ENV = "LIVE_TEST_INFRASTRUCTURE"
CONFIG_ENV = "LIVE_TEST_INFRASTRUCTURE_CONFIG"


class ExternalDependencies(BaseModel):
    """The supplied PostgreSQL database is administrative, never the test database."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    postgres_admin_url: SecretStr
    redis_url: SecretStr
    object_endpoint_url: str
    object_region: str = "us-east-1"
    aws_access_key_id: SecretStr
    aws_secret_access_key: SecretStr

    @field_validator("postgres_admin_url", "redis_url", "object_endpoint_url")
    @classmethod
    def loopback_endpoint(cls, value, info):
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        parts = urlsplit(raw)
        scheme = {"postgres_admin_url": "postgresql", "redis_url": "redis", "object_endpoint_url": "http"}
        if parts.scheme != scheme[info.field_name] or parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Live infrastructure requires explicit loopback endpoints")
        if parts.query or parts.fragment:
            raise ValueError("Infrastructure endpoint query strings and fragments are unsupported")
        if info.field_name == "object_endpoint_url" and (
            parts.username or parts.password or parts.path not in {"", "/"}
        ):
            raise ValueError("S3 requires an origin without embedded credentials")
        return value

    @field_validator("aws_access_key_id", "aws_secret_access_key")
    @classmethod
    def nonempty_credential(cls, value):
        if not value.get_secret_value():
            raise ValueError("Explicit S3 credentials must not be empty")
        return value

    def object_options(self):
        return {
            "endpoint_url": self.object_endpoint_url,
            "region": self.object_region,
            "credentials": {
                "aws_access_key_id": self.aws_access_key_id.get_secret_value(),
                "aws_secret_access_key": self.aws_secret_access_key.get_secret_value(),
                "aws_session_token": "",
            },
        }


def load_external_dependencies():
    mode = os.environ.get(MODE_ENV, "docker")
    if mode == "docker":
        return None
    if mode != "external":
        raise ValueError("Live infrastructure must be docker or external")
    path = os.environ.get(CONFIG_ENV)
    if not path:
        raise ValueError("External infrastructure requires --infrastructure-config or LIVE_TEST_INFRASTRUCTURE_CONFIG")
    try:
        return ExternalDependencies.model_validate(tomllib.loads(Path(path).read_text()))
    except (OSError, ValueError):
        # Do not include configuration values or validation input in CI output.
        raise ValueError(
            "Cannot load external live infrastructure configuration; check the documented TOML fields"
        ) from None


def add_infrastructure_arguments(parser):
    parser.add_argument("--infrastructure", choices=("docker", "external"), default="docker")
    parser.add_argument(
        "--infrastructure-config", type=Path, help="Explicit TOML endpoints for external infrastructure"
    )


def infrastructure_environment(options):
    if options.infrastructure == "external" and options.infrastructure_config is None:
        raise ValueError("--infrastructure=external requires --infrastructure-config")
    if options.infrastructure == "docker" and options.infrastructure_config is not None:
        raise ValueError("--infrastructure-config requires --infrastructure=external")
    environment = {MODE_ENV: options.infrastructure}
    if options.infrastructure_config is not None:
        environment[CONFIG_ENV] = str(options.infrastructure_config.resolve())
    return environment


@asynccontextmanager
async def external_database(dependencies):
    """Create/drop only a randomly named database allocated by this invocation."""
    admin_url = dependencies.postgres_admin_url.get_secret_value()
    name = "a13n_live_" + uuid4().hex
    options = {"autocommit": True, "connect_timeout": 5, "options": "-c statement_timeout=30000"}
    try:
        async with await psycopg.AsyncConnection.connect(admin_url, **options) as connection:
            await connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        print(f"Created isolated database {name} on external PostgreSQL", flush=True)
        yield (
            make_url(admin_url)
            .set(drivername="postgresql+psycopg", database=name)
            .render_as_string(hide_password=False)
        )
    finally:
        # A lost CREATE reply can still have committed. This random name belongs
        # to this invocation, so cleanup also covers partially completed startup.
        with anyio.CancelScope(shield=True), anyio.fail_after(40):
            async with await psycopg.AsyncConnection.connect(admin_url, **options) as connection:
                await connection.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )
        print(f"Removed isolated database {name}; external PostgreSQL retained", flush=True)
