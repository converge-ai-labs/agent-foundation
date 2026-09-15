"""Typed configuration for Service storage resources."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class PostgreSQLConfig(_Config):
    url: SecretStr
    pool_size: int = Field(default=10, ge=1, le=1000)
    max_overflow: int = Field(default=20, ge=0, le=1000)
    pool_timeout_seconds: float = Field(default=30, gt=0, le=300)
    pool_recycle_seconds: int = Field(default=3600, ge=0)
    connect_timeout_seconds: int = Field(default=10, ge=1, le=300)
    statement_timeout_seconds: float = Field(default=30, gt=0, le=3600)
    cleanup_timeout_seconds: float = Field(default=5, gt=0, le=60)

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, value: str | SecretStr) -> SecretStr:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        try:
            url = make_url(raw)
        except (ArgumentError, TypeError, ValueError):
            raise ValueError("database.url must be a valid PostgreSQL URL") from None
        if url.drivername not in {"postgresql", "postgresql+psycopg"}:
            raise ValueError("PostgreSQL requires a postgresql or postgresql+psycopg URL")
        return SecretStr(url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False))


class RedisServerConfig(_Config):
    backend: Literal["redis"] = "redis"
    url: SecretStr
    max_connections: int = Field(default=20, ge=1, le=1000)
    connect_timeout_seconds: float = Field(default=5, gt=0, le=300)
    command_timeout_seconds: float = Field(default=10, gt=0, le=300)
    health_check_interval_seconds: float = Field(default=30, gt=0, le=3600)
    cleanup_timeout_seconds: float = Field(default=5, gt=0, le=60)


class RedisMemoryConfig(_Config):
    backend: Literal["memory"] = "memory"
    server_version: tuple[int, ...] = (8, 0)
    cleanup_timeout_seconds: float = Field(default=5, gt=0, le=60)

    @model_validator(mode="after")
    def validate_version(self) -> "RedisMemoryConfig":
        if not self.server_version or any(part < 0 for part in self.server_version):
            raise ValueError("server_version must contain non-negative integers")
        return self


RedisConfig = Annotated[RedisServerConfig | RedisMemoryConfig, Field(discriminator="backend")]


class S3ObjectConfig(_Config):
    backend: Literal["s3"] = "s3"
    bucket: str = Field(min_length=1)
    region: str = Field(default="us-east-1", min_length=1)
    endpoint_url: str | None = None
    force_path_style: bool = False
    connect_timeout_seconds: float = Field(default=5, gt=0, le=300)
    read_timeout_seconds: float = Field(default=30, gt=0, le=300)
    compatibility_timeout_seconds: float = Field(default=120, gt=0, le=600)
    max_pool_connections: int = Field(default=20, ge=1, le=1000)
    multipart_part_size: int = Field(
        default=8 * 1024 * 1024,
        ge=5 * 1024 * 1024,
        le=64 * 1024 * 1024,
    )


class LocalObjectConfig(_Config):
    backend: Literal["local"] = "local"
    root: Path
    create_root: bool = True
    chunk_size: int = Field(default=256 * 1024, ge=4096, le=8 * 1024 * 1024)


ObjectStoreConfig = Annotated[S3ObjectConfig | LocalObjectConfig, Field(discriminator="backend")]


class FilesystemConfig(_Config):
    root: Path
    create_root: bool = True
    worker_limit: int = Field(default=20, ge=1, le=256)


class StorageSettings(_Config):
    database: PostgreSQLConfig
    redis: RedisConfig
    objects: ObjectStoreConfig
    filesystem: FilesystemConfig

    @model_validator(mode="after")
    def validate_local_paths(self) -> "StorageSettings":
        files_root = _normalized(self.filesystem.root)
        if isinstance(self.objects, LocalObjectConfig):
            object_root = _normalized(self.objects.root)
            if _overlaps(files_root, object_root):
                raise ValueError("filesystem and local object roots must not overlap")
        return self


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _normalized(path: Path) -> Path:
    return path.resolve(strict=False)
