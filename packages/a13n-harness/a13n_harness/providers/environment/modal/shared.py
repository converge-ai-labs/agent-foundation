"""Modal shared implementation."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SecretStr, model_validator

from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.environment.variables import EnvironmentVariables
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.environment.modal.pool import Transport, TransportKey, TransportPool
from a13n_harness.providers.environment.native.configuration import TargetState
from a13n_harness.providers.environment.native.environment import NativeReference, NativeRuntime
from a13n_harness.providers.environment.native.errors import failure

if TYPE_CHECKING:
    import modal


class ModalConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    _endpoint_policy: EndpointPolicy = PrivateAttr(default_factory=EndpointPolicy)
    workspace: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Optional workspace label; credentials select the Modal account.",
    )
    app_name: str = Field(
        default="a13n-sandboxes",
        min_length=1,
        max_length=128,
        description="Modal App for named sandboxes. Automatic desktop preparation creates it if missing.",
    )
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
    environment: EnvironmentVariables = Field(default_factory=dict)
    request_timeout_seconds: float = Field(default=120, gt=0, le=600, allow_inf_nan=False)
    timeout_seconds: int = Field(default=86400, ge=300, le=86400)
    cpu: float = Field(default=1, gt=0, le=64, allow_inf_nan=False)
    memory: int = Field(default=1024, ge=128, le=262144, description="Memory in MiB")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            self.model_dump_json(exclude={"environment"} if not self.environment else set()).encode()
        ).hexdigest()


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


@dataclass(frozen=True, slots=True)
class ModalRuntime(NativeRuntime):
    pool: TransportPool | None = None
    account: tuple[str, int, str | None] | None = None


class ModalReference(NativeReference[ModalEnvironmentConfiguration, ModalState]):
    reuse_transport = False

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
        self._pool = runtime.pool if isinstance(runtime, ModalRuntime) else None
        self._account = runtime.account if isinstance(runtime, ModalRuntime) else None
        self._lease: Transport | None = None
        self._discard = False

    async def lookup(self) -> modal.Sandbox | None:
        import modal

        client = await self.connection()
        try:
            with sdk_errors():
                sandbox = None
                if self.target:
                    try:
                        candidate = self._lease.sandbox if self._lease is not None else None
                        if candidate is None:
                            candidate = await modal.Sandbox.from_id.aio(self.target.target_id, client=client)
                            if self._lease is not None:
                                self._lease.sandbox = candidate
                        self.sandbox = candidate
                        self.validate_labels(await candidate.get_tags.aio())
                        status = await candidate.poll.aio()
                        if status is None:
                            sandbox = candidate
                    except modal.exception.NotFoundError:
                        self.discard_transport()
                if self.target is None:
                    try:
                        sandbox = await modal.Sandbox.from_name.aio(
                            self.backend.app_name,
                            self.name,
                            environment_name=self.backend.environment_name,
                            client=client,
                        )
                    except modal.exception.NotFoundError:
                        return None
                    self.validate_labels(await sandbox.get_tags.aio())
                if sandbox is None:
                    self.discard_transport()
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
        except BaseException:
            self.discard_transport()
            raise

    async def connection(self) -> modal.Client:
        import modal
        from modal.config import config as modal_config
        from modal_proto import api_pb2

        if self.client is None:
            if self.reuse_transport and self._pool is not None and self.target is not None:
                self._lease = await self._pool.acquire(self.transport_key())
                self.client = self._lease.client
            if self.client is not None:
                return self.client
            with sdk_errors():
                # Avoid from_credentials' implicit process-lifetime shutdown registry.
                # The explicit pool, or this reference when unpooled, owns cleanup.
                self.client = modal.Client(
                    modal_config["server_url"],
                    api_pb2.CLIENT_TYPE_CLIENT,
                    (self.credential.token_id.get_secret_value(), self.credential.token_secret.get_secret_value()),
                )
                if self._lease is not None:
                    self._lease.client = self.client
                try:
                    await self.client.__aenter__()
                except BaseException:
                    self.discard_transport()
                    raise
        return self.client

    async def status(self) -> Literal["running", "stopped", "absent"]:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            if await self.lookup() is not None:
                return "running"
            return "stopped" if self.target and self.target.snapshot_id else "absent"

    def transport_key(self) -> TransportKey:
        from modal.config import config as modal_config

        assert self._account is not None and self.target is not None
        return TransportKey(
            self._account,
            self.backend.model_dump_json(),
            modal_config["server_url"],
            self.owner,
            self.config.fingerprint,
            self.target.target_id,
        )

    def discard_transport(self) -> None:
        self._discard = True

    async def invalidate_cached_transport(self) -> None:
        if self._pool is not None and self.target is not None:
            await self._pool.invalidate(self.transport_key())

    async def close_transport(self) -> None:
        lease, self._lease = self._lease, None
        sandbox, client = self.sandbox, self.client
        self.sandbox = self.client = None
        if lease is not None:
            assert self._pool is not None
            await self._pool.release(lease, discard=self._discard)
            return
        try:
            if sandbox is not None:
                await sandbox.detach.aio()
        finally:
            if client is not None:
                await client.__aexit__(None, None, None)
