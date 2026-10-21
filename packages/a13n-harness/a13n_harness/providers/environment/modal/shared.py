"""Modal shared implementation."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SecretStr, model_validator

from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.environment.modal.diagnostics import measure
from a13n_harness.providers.environment.native.configuration import TargetState
from a13n_harness.providers.environment.native.environment import NativeReference, NativeRuntime
from a13n_harness.providers.environment.native.errors import failure

if TYPE_CHECKING:
    import modal


class ModalConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    _endpoint_policy: EndpointPolicy = PrivateAttr(default_factory=EndpointPolicy)
    workspace: str = Field(min_length=1, max_length=128, description="Modal workspace owning the credentials and App.")
    app_name: str = Field(min_length=1, max_length=128, description="Existing deployed Modal App for named sandboxes.")
    environment_name: str = Field(default="main", min_length=1, max_length=128)


class ModalCredential(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    token_id: SecretStr = Field(min_length=1)
    token_secret: SecretStr = Field(min_length=1)


class ModalEnvironmentConfiguration(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra={"x-primary-fields": ["image", "desktop", "timeout_seconds", "cpu", "memory"]},
    )
    image: str = Field(
        min_length=1,
        max_length=256,
        description="Operator-built registry image or Modal image ID (im-...) containing compatible a13n-envd and sandbox launchers. Agent operations use EIP.",
    )
    desktop: bool = Field(default=False, description="Require an observable, input-ready X11 desktop from the image.")
    request_timeout_seconds: float = Field(default=120, gt=0, le=600, allow_inf_nan=False)
    timeout_seconds: int = Field(default=86400, ge=300, le=86400)
    cpu: float = Field(default=1, gt=0, le=64, allow_inf_nan=False)
    memory: int = Field(default=1024, ge=128, le=262144, description="Memory in MiB")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


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
            with measure("command_start"):
                process = await sandbox.exec.aio(*argv, timeout=int(timeout))
            with measure("command_stdout"):
                stdout = await process.stdout.read.aio()
            with measure("command_wait"):
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
                    with measure("sandbox_from_id"):
                        candidate = await modal.Sandbox.from_id.aio(self.target.target_id, client=client)
                    with measure("sandbox_get_tags"):
                        self.validate_labels(await candidate.get_tags.aio())
                    with measure("sandbox_poll"):
                        status = await candidate.poll.aio()
                    if status is None:
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
        from modal.config import config as modal_config
        from modal_proto import api_pb2

        if self.client is None:
            with measure("client_open"), sdk_errors():
                # from_credentials registers a process-lifetime shutdown task that retains each short-lived client.
                # This reference owns the client and releases it in close_transport.
                self.client = modal.Client(
                    modal_config["server_url"],
                    api_pb2.CLIENT_TYPE_CLIENT,
                    (self.credential.token_id.get_secret_value(), self.credential.token_secret.get_secret_value()),
                )
                await self.client.__aenter__()
        return self.client

    async def status(self) -> Literal["running", "stopped", "absent"]:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            if await self.lookup() is not None:
                return "running"
            return "stopped" if self.target and self.target.snapshot_id else "absent"

    async def close_transport(self) -> None:
        try:
            if self.sandbox:
                with measure("sandbox_detach"):
                    await self.sandbox.detach.aio()
        finally:
            if self.client:
                with measure("client_close"):
                    await self.client.__aexit__(None, None, None)
