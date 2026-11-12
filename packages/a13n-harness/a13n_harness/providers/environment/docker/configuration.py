"""Native Docker creation configuration and durable container identity."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from a13n_harness.environment.envd_settings import EnvdLimits, EnvdSettings
from a13n_harness.environment.models import EnvironmentTargetState
from a13n_harness.environment.variables import EnvironmentVariables

DEFAULT_DOCKER_IMAGE = "ghcr.io/converge-ai-labs/a13n-sandbox:dev"


class DockerMountConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    target: PurePosixPath
    read_only: bool = True

    @field_validator("source")
    @classmethod
    def source_path(cls, value: str) -> str:
        if not value.startswith("/") or ".." in PurePosixPath(value).parts or "\x00" in value:
            raise ValueError("Host mount source must be an absolute normalized path")
        return value

    @field_validator("target")
    @classmethod
    def target_path(cls, value: PurePosixPath) -> PurePosixPath:
        _absolute(value)
        if value == PurePosixPath("/") or value == PurePosixPath("/workspace"):
            raise ValueError("External mounts cannot replace the container root or workspace")
        if value.is_relative_to("/tmp/a13n") or value in PurePosixPath("/tmp/a13n").parents:
            raise ValueError("External mounts cannot replace the private command directory")
        return value


class DockerEnvironmentConfiguration(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra={
            "x-primary-fields": [
                "image",
                "execution",
                "desktop",
                "environment",
                "init_script",
                "disable_network",
                "cpus",
                "memory_gb",
                "mounts",
            ]
        },
    )

    image: Annotated[str, Field(min_length=1, max_length=1024)] = DEFAULT_DOCKER_IMAGE
    execution: Literal["native", "envd_http", "envd_ws"] = Field(
        default="native",
        title="Execution connection",
        json_schema_extra={
            "oneOf": [
                {"const": "native", "title": "Native Docker"},
                {"const": "envd_http", "title": "Envd HTTP (EIP)"},
                {"const": "envd_ws", "title": "Envd outbound WebSocket (Service)"},
            ]
        },
        description="Native Docker exec, Envd HTTP, or Service-managed outbound WebSocket. HTTP alone requires inbound routing.",
    )
    desktop: bool = Field(
        default=False,
        title="X11 desktop",
        description=(
            "Require an observable, input-ready desktop from the image-managed envd. "
            "Requires envd_http or envd_ws execution and a desktop image. Computer permissions remain separate."
        ),
    )
    mounts: tuple[DockerMountConfiguration, ...] = Field(
        default=(),
        title="Host directory mounts",
        description="Existing host directories; source, target and read_only. External data survives Environment deletion.",
    )
    environment: EnvironmentVariables = Field(default_factory=dict)
    init_script: str | None = Field(
        default=None,
        max_length=1_048_576,
        title="Initialization script",
        description="Runs once on a newly created container. Do not put secrets in this template.",
        json_schema_extra={"format": "multiline"},
    )
    disable_network: bool = Field(default=False, title="Disable networking")
    user: str | None = Field(default=None, min_length=1, max_length=128)
    shell: str = "/bin/sh"
    python: str = "python3"
    cpus: float | None = Field(default=None, ge=0.001, allow_inf_nan=False, title="CPU cores")
    memory_gb: float | None = Field(default=None, ge=0.006291456, allow_inf_nan=False, title="Memory (GB)")
    pids_limit: int | None = Field(default=None, gt=0)
    stop_grace_seconds: int = Field(default=10, ge=0, le=300)
    request_timeout_seconds: int = Field(
        default=60,
        gt=0,
        le=3600,
        description="Bounds one internal helper operation, including an initialization script; not an Agent Run or ordinary shell duration.",
    )
    max_file_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    max_query_entries: int = Field(default=100_000, gt=0)
    max_output_preview_bytes: int = Field(default=64 * 1024, gt=0)
    max_output_bytes_per_stream: int = Field(default=16 * 1024 * 1024, gt=0)
    max_spool_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    max_concurrent_processes: int = Field(default=128, gt=0)

    @field_validator("image", "shell", "python")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or value != value.strip() or "\x00" in value:
            raise ValueError("Docker command and image values must be nonblank and contain no NUL")
        return value

    @field_validator("memory_gb")
    @classmethod
    def valid_memory_limit(cls, value: float | None) -> float | None:
        if value is not None and (value > (2**63 - 1) / 1_000_000_000 or int(value * 1_000_000_000) > 2**63 - 1):
            raise ValueError("Docker memory limit exceeds signed 64-bit bytes")
        return value

    @model_validator(mode="after")
    def consistent(self) -> Self:
        targets = [mount.target for mount in self.mounts]
        if len(set(targets)) != len(targets):
            raise ValueError("Docker mount destinations must be unique")
        if any(not key or "=" in key or "\x00" in key + value for key, value in self.environment.items()):
            raise ValueError("Invalid container environment variables")
        if self.execution == "native" and self.max_output_preview_bytes > self.max_output_bytes_per_stream:
            raise ValueError("Output preview exceeds the per-stream capture limit")
        if self.desktop and self.execution == "native":
            raise ValueError("Desktop capability requires envd_http or envd_ws execution")
        if self.execution != "native":
            self.launch_limits()
            if self.disable_network:
                raise ValueError("Envd execution requires networking")
            if self.user is not None:
                raise ValueError("Envd images own daemon and Session identities; do not override user")
            if any(key.startswith("A13N_ENVD_") or key in {"DISPLAY", "XAUTHORITY"} for key in self.environment):
                raise ValueError("Envd execution owns daemon configuration and display variables")
        return self

    envd: EnvdSettings = Field(
        default_factory=EnvdSettings,
        exclude_if=lambda value: not value.overrides(),
        description="Envd startup settings. Applies to new instances; existing instances keep their frozen settings.",
    )

    @field_validator("envd", mode="before")
    @classmethod
    def validate_envd_settings(cls, value: Any, info: ValidationInfo) -> Any:
        # Saved Envd overrides keep the same Docker fallbacks across execution modes.
        defaults = {
            "max_file_bytes": info.data.get("max_file_bytes", cls.model_fields["max_file_bytes"].default),
            "max_output_preview_bytes": info.data.get(
                "max_output_preview_bytes", cls.model_fields["max_output_preview_bytes"].default
            ),
            "max_output_bytes_per_stream": info.data.get(
                "max_output_bytes_per_stream", cls.model_fields["max_output_bytes_per_stream"].default
            ),
            "max_spool_bytes": info.data.get("max_spool_bytes", cls.model_fields["max_spool_bytes"].default),
            "max_processes": info.data.get(
                "max_concurrent_processes", cls.model_fields["max_concurrent_processes"].default
            ),
        }
        return EnvdSettings.model_validate(value, context={"envd_limit_defaults": defaults})

    def launch_limits(self) -> dict[str, int]:
        limits = {
            "max_file_bytes": self.max_file_bytes,
            "max_output_preview_bytes": self.max_output_preview_bytes,
            "max_output_bytes_per_stream": self.max_output_bytes_per_stream,
            "max_spool_bytes": self.max_spool_bytes,
            "max_processes": self.max_concurrent_processes,
            **self.envd.limits.overrides(),
        }
        EnvdLimits.model_validate(limits)
        return limits


class DockerProviderStateData(EnvironmentTargetState):
    container_id: str


def _absolute(value: PurePosixPath) -> None:
    if not value.is_absolute() or ".." in value.parts or "\x00" in str(value):
        raise ValueError("Container path must be absolute and normalized")
