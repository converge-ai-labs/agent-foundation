"""Opt-in [s3] settings from the shared private Provider configuration."""

import os
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from aiobotocore.config import AioConfig
from aiobotocore.httpxsession import HttpxSession
from aiobotocore.session import get_session
from botocore.configprovider import ConfigValueStore
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from ..providers.provider_config import DEFAULT_PATH, provider_config_path

STATE_ROOT = DEFAULT_PATH.parent / ".state" / "s3-benchmarks"


class S3ConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    dedicated_test_bucket: Literal[True]
    endpoint_url: str
    region: str = Field(min_length=1)
    bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    access_key: SecretStr
    secret_key: SecretStr
    session_token: SecretStr = SecretStr("")

    @field_validator("endpoint_url")
    @classmethod
    def endpoint_is_explicit(cls, value):
        parsed = urlsplit(value)
        loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if (
            not parsed.hostname
            or parsed.scheme not in ({"http", "https"} if loopback else {"https"})
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("endpoint requires HTTPS (HTTP allowed only on loopback), with no credentials or path")
        return value

    @field_validator("access_key", "secret_key")
    @classmethod
    def credentials_are_explicit(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError("explicit test credentials are required")
        return value


def load_s3_config(*, enabled: bool, path: Path | None = None) -> S3ConnectionConfig | None:
    if not enabled:
        return None
    selected = provider_config_path(path)
    if not selected.exists() and path is None and not os.environ.get("LIVE_TEST_PROVIDERS_CONFIG"):
        return None
    try:
        if selected.is_symlink() or not selected.is_file() or selected.stat().st_mode & 0o077:
            raise ValueError("S3 benchmark configuration must be a private regular file (chmod 600)")
        with selected.open("rb") as source:
            values = tomllib.load(source).get("s3")
        if isinstance(values, dict):
            values = {
                key: value.strip() if isinstance(value, str) else value
                for key, value in values.items()
                if not (isinstance(value, str) and not value.strip())
            } or None
        return None if values is None else S3ConnectionConfig.model_validate(values)
    except (OSError, tomllib.TOMLDecodeError, ValidationError):
        # TOML errors may quote a source line containing a secret.
        raise ValueError("Invalid S3 benchmark [s3] configuration; see providers.example.toml") from None


@asynccontextmanager
async def open_s3_client(config: S3ConnectionConfig, *, pool_size=64):
    session = get_session()
    # Replace the provider chain, including AWS_PROFILE and nested S3 settings.
    # Setting profile=None on Session alone falls through to environment lookup.
    config_store = ConfigValueStore()
    for name, (_, _, default, _) in session.session_var_map.items():
        config_store.set_config_variable(name, default)
    config_store.set_config_variable("config_file", os.devnull)
    config_store.set_config_variable("credentials_file", os.devnull)
    session.register_component("config_store", config_store)
    async with session.create_client(
        "s3",
        endpoint_url=config.endpoint_url,
        region_name=config.region,
        aws_access_key_id=config.access_key.get_secret_value(),
        aws_secret_access_key=config.secret_key.get_secret_value(),
        aws_session_token=config.session_token.get_secret_value(),
        verify=True,
        config=AioConfig(
            connect_timeout=5,
            read_timeout=30,
            max_pool_connections=pool_size,
            proxies={},
            retries={"total_max_attempts": 1, "mode": "standard"},
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            ignore_configured_endpoint_urls=True,
            http_session_cls=HttpxSession,
        ),
    ) as client:
        yield client
