"""Modal shared implementation."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from ..errors import EnvironmentProviderError
from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..models import EnvironmentState
from ..native.configuration import CommandConfiguration, TargetState
from ..native.environment import NativeReference, NativeRuntime
from ..native.errors import failure

if TYPE_CHECKING:
    import modal


class ModalConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    workspace: str = Field(min_length=1, max_length=128, description="Modal workspace owning the credentials and App.")
    app_name: str = Field(min_length=1, max_length=128, description="Existing deployed Modal App for named sandboxes.")
    environment_name: str = Field(default="main", min_length=1, max_length=128)


class ModalCredential(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    token_id: SecretStr = Field(min_length=1)
    token_secret: SecretStr = Field(min_length=1)


class ModalEnvironmentConfiguration(CommandConfiguration):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra={"x-primary-fields": ["image", "timeout_seconds", "cpu", "memory"]},
    )
    image: str = Field(default="python:3.13-slim", min_length=1, max_length=256)
    python: str = "/usr/local/bin/python3"
    timeout_seconds: int = Field(default=86400, ge=300, le=86400)
    cpu: float = Field(default=1, gt=0, le=64, allow_inf_nan=False)
    memory: int = Field(default=1024, ge=128, le=262144, description="Memory in MiB")


class SnapshotReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    image_id: str = Field(pattern=r"^im-[A-Za-z0-9]+$")
    sandbox_id: str = Field(pattern=r"^sb-[A-Za-z0-9-]+$")


class ModalState(TargetState):
    snapshot_id: str | None = Field(default=None, pattern=r"^im-[A-Za-z0-9]+$")
    snapshot_source_id: str | None = Field(default=None, pattern=r"^sb-[A-Za-z0-9-]+$")
    retired_snapshots: tuple[SnapshotReference, ...] = ()

    @model_validator(mode="after")
    def paired_snapshot(self) -> Self:
        if (self.snapshot_id is None) != (self.snapshot_source_id is None):
            raise ValueError("Snapshot image and source must be supplied together")
        return self


@contextmanager
def sdk_errors(*, mutation: bool = False) -> Iterator[None]:
    import modal

    try:
        yield
    except EnvironmentProviderError:
        raise
    except modal.exception.AuthError:
        raise failure("modal", "provider_denied", Category.DENIED) from None
    except Exception:
        raise failure(
            "modal",
            "provider_unknown_outcome" if mutation else "provider_unavailable",
            Category.UNKNOWN_OUTCOME if mutation else Category.UNAVAILABLE,
            certainty=Certainty.UNKNOWN if mutation else Certainty.KNOWN,
        ) from None


async def execute_command(sandbox: modal.Sandbox, argv: list[str], timeout: float) -> str:
    with sdk_errors(mutation=True):
        async with asyncio.timeout(timeout + 5):
            process = await sandbox.exec.aio(*argv, timeout=int(timeout))
            stdout = await process.stdout.read.aio()
            await process.wait.aio()
            if process.returncode != 0:
                raise failure(
                    "modal",
                    "provider_command_failed",
                    Category.PROVIDER_FAILURE,
                    certainty=Certainty.KNOWN,
                )
            if len(stdout.encode()) > 24 * 1024 * 1024:
                raise failure(
                    "modal",
                    "provider_response_invalid",
                    Category.UNKNOWN_OUTCOME,
                    certainty=Certainty.UNKNOWN,
                )
            return stdout


class ModalReference(NativeReference[ModalEnvironmentConfiguration, ModalState]):
    def __init__(
        self,
        config: ModalEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
    ):
        super().__init__("modal", config, environment_id, state, state_model=ModalState)
        assert isinstance(runtime.configuration, ModalConnectionConfiguration)
        assert isinstance(runtime.credential, ModalCredential)
        self.backend = runtime.configuration
        self.credential = runtime.credential
        self.client: modal.Client | None = None
        self.sandbox: modal.Sandbox | None = None

    async def lookup(self) -> modal.Sandbox | None:
        import modal

        client = await self.connection()
        with sdk_errors():
            sandbox = None
            if self.target:
                try:
                    candidate = await modal.Sandbox.from_id.aio(self.target.target_id, client=client)
                    self.validate_labels(await candidate.get_tags.aio())
                    if await candidate.poll.aio() is None:
                        sandbox = candidate
                except modal.exception.NotFoundError:
                    pass
            if self.target is None:
                try:
                    sandbox = await modal.Sandbox.from_name.aio(
                        self.backend.app_name, self.name, environment_name=self.backend.environment_name, client=client
                    )
                except modal.exception.NotFoundError:
                    return None
                self.validate_labels(await sandbox.get_tags.aio())
            if sandbox is None:
                return None
            if self.target:
                self.observe(self.target.model_copy(update={"target_id": sandbox.object_id}))
            else:
                self.observe(
                    ModalState(
                        target_id=sandbox.object_id,
                        environment_id=self.owner,
                        configuration_fingerprint=self.config.fingerprint,
                    )
                )
            return sandbox

    async def connection(self) -> modal.Client:
        import modal

        if self.client is None:
            with sdk_errors():
                self.client = await modal.Client.from_credentials.aio(
                    self.credential.token_id.get_secret_value(), self.credential.token_secret.get_secret_value()
                )
        return self.client

    async def status(self) -> Literal["running", "stopped", "absent"]:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            if await self.lookup() is not None:
                return "running"
            return "stopped" if self.target and self.target.snapshot_id else "absent"

    async def close_transport(self) -> None:
        try:
            if self.sandbox:
                await self.sandbox.detach.aio()
        finally:
            if self.client:
                await self.client.__aexit__(None, None, None)
