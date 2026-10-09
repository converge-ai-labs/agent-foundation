"""Modal async SDK operations and provider-owned filesystem snapshot stop/resume."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Self

if TYPE_CHECKING:
    import modal
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from ..errors import EnvironmentProviderError
from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..models import EnvironmentState
from ..native.configuration import CommandConfiguration, TargetState
from ..native.environment import NativeRuntime, NativeTarget, native_definition
from ..native.errors import failure


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


class ModalTarget(NativeTarget[ModalEnvironmentConfiguration, ModalState]):
    def __init__(
        self,
        config: ModalEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
        operation_id: str,
    ):
        del operation_id
        super().__init__("modal", config, environment_id, state, state_model=ModalState)
        assert isinstance(runtime.configuration, ModalConnectionConfiguration)
        assert isinstance(runtime.credential, ModalCredential)
        self.backend = runtime.configuration
        self.credential = runtime.credential
        self.client: modal.Client | None = None
        self.sandbox: modal.Sandbox | None = None

    async def connection(self) -> modal.Client:
        import modal

        if self.client is None:
            with sdk_errors():
                self.client = await modal.Client.from_credentials.aio(
                    self.credential.token_id.get_secret_value(), self.credential.token_secret.get_secret_value()
                )
        return self.client

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
                self.remember(self.target.model_copy(update={"target_id": sandbox.object_id}))
            else:
                self.remember(
                    ModalState(
                        target_id=sandbox.object_id,
                        environment_id=self.owner,
                        configuration_fingerprint=self.config.fingerprint,
                    )
                )
            return sandbox

    async def _allocate(self, snapshot: SnapshotReference | None) -> modal.Sandbox:
        import modal

        client = await self.connection()
        if snapshot is not None:
            await self.validate_snapshot(snapshot)
        with sdk_errors(mutation=True):
            app = await modal.App.lookup.aio(
                self.backend.app_name, environment_name=self.backend.environment_name, client=client
            )
            image = (
                modal.Image.from_id(snapshot.image_id, client=client)
                if snapshot
                else modal.Image.from_registry(self.config.image)
            )
            expiry = datetime.now(UTC) + timedelta(seconds=self.config.timeout_seconds)
            tags = {**self.labels, "a13n_expiry": expiry.isoformat()}
            if snapshot is not None:
                tags["a13n_resume_snapshot"] = snapshot.image_id
            sandbox = await modal.Sandbox.create.aio(
                app=app,
                name=self.name,
                image=image,
                client=client,
                tags=tags,
                timeout=self.config.timeout_seconds,
                cpu=self.config.cpu,
                memory=self.config.memory,
            )
        self._remember_running(sandbox)
        return sandbox

    def _remember_running(self, sandbox: modal.Sandbox) -> None:
        if self.target is not None:
            self.remember(self.target.model_copy(update={"target_id": sandbox.object_id}))
        else:
            self.remember(
                ModalState(
                    target_id=sandbox.object_id,
                    environment_id=self.owner,
                    configuration_fingerprint=self.config.fingerprint,
                )
            )

    async def _finish_start(self, sandbox: modal.Sandbox) -> None:
        self.sandbox = sandbox
        # Readiness and snapshot retirement belong to explicit management. A
        # failed probe retains both the new target ID and the resume snapshot.
        await self.execute([self.config.python, "-I", "-c", "pass"], self.config.request_timeout_seconds)
        if self.target and self.target.snapshot_id:
            self.remember(
                self.target.model_copy(
                    update={
                        "retired_snapshots": (
                            *self.target.retired_snapshots,
                            SnapshotReference(
                                image_id=self.target.snapshot_id,
                                sandbox_id=self.target.snapshot_source_id or self.target.target_id,
                            ),
                        ),
                        "snapshot_id": None,
                        "snapshot_source_id": None,
                    }
                )
            )
        await self.cleanup_snapshots()

    async def create(self) -> None:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            sandbox = await self.lookup()
            if sandbox is None:
                if self.target is not None:
                    raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
                sandbox = await self._allocate(None)
            await self._finish_start(sandbox)

    async def start(self) -> None:
        import modal

        async with asyncio.timeout(self.config.request_timeout_seconds):
            sandbox = await self.lookup()
            if sandbox is None:
                await self.adopt_snapshot()
                if self.target is None or self.target.snapshot_id is None or self.target.snapshot_source_id is None:
                    raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
                snapshot = SnapshotReference(
                    image_id=self.target.snapshot_id, sandbox_id=self.target.snapshot_source_id
                )
                await self.validate_snapshot(snapshot)
                # Reconcile a previous snapshot-backed resume whose response was
                # lost. Ordinary lookup of a saved target never changes its ID.
                with sdk_errors():
                    try:
                        sandbox = await modal.Sandbox.from_name.aio(
                            self.backend.app_name,
                            self.name,
                            environment_name=self.backend.environment_name,
                            client=await self.connection(),
                        )
                    except modal.exception.NotFoundError:
                        sandbox = None
                    if sandbox is not None:
                        tags = await sandbox.get_tags.aio()
                        self.validate_labels(tags)
                        if tags.get("a13n_resume_snapshot") != snapshot.image_id:
                            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
                        self._remember_running(sandbox)
                if sandbox is None:
                    sandbox = await self._allocate(snapshot)
            await self._finish_start(sandbox)

    async def open(self, *, execution_id: str) -> None:
        self.require_target()
        async with asyncio.timeout(self.config.request_timeout_seconds):
            sandbox = await self.lookup()
            if sandbox is None:
                category = Category.CONFLICT if self.target and self.target.snapshot_id else Category.MISSING
                raise failure(self.provider_key, "provider_target_unavailable", category)
            self.sandbox = sandbox
            await self.open_operations(self.execute, sandbox.object_id, execution_id)

    async def execute(self, argv: list[str], timeout: float) -> str:
        assert self.sandbox
        with sdk_errors(mutation=True):
            async with asyncio.timeout(timeout + 5):
                process = await self.sandbox.exec.aio(*argv, timeout=int(timeout))
                stdout = await process.stdout.read.aio()
                await process.wait.aio()
                if process.returncode != 0:
                    raise failure(
                        self.provider_key,
                        "provider_command_failed",
                        Category.PROVIDER_FAILURE,
                        certainty=Certainty.KNOWN,
                    )
                if len(stdout.encode()) > 24 * 1024 * 1024:
                    raise failure(
                        self.provider_key,
                        "provider_response_invalid",
                        Category.UNKNOWN_OUTCOME,
                        certainty=Certainty.UNKNOWN,
                    )
                return stdout

    async def inspect(self) -> Literal["running", "stopped", "absent"]:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            if await self.lookup() is not None:
                return "running"
            return "stopped" if self.target and self.target.snapshot_id else "absent"

    async def stop(self) -> None:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            sandbox = await self.lookup()
            if sandbox is None:
                await self.adopt_snapshot()
                return
            assert self.target
            self.sandbox = sandbox
            with sdk_errors(mutation=True):
                if self.target.snapshot_id and self.target.snapshot_source_id == sandbox.object_id:
                    image_id = self.target.snapshot_id
                else:
                    image = await sandbox.snapshot_filesystem.aio(
                        timeout=int(self.config.request_timeout_seconds), ttl=None
                    )
                    image_id = image.object_id
                    retired = self.target.retired_snapshots
                    if self.target.snapshot_id:
                        retired = (
                            *retired,
                            SnapshotReference(
                                image_id=self.target.snapshot_id,
                                sandbox_id=self.target.snapshot_source_id or self.target.target_id,
                            ),
                        )
                    self.remember(
                        self.target.model_copy(
                            update={
                                "snapshot_id": image_id,
                                "snapshot_source_id": sandbox.object_id,
                                "retired_snapshots": retired,
                            }
                        )
                    )
                tags = await sandbox.get_tags.aio()
                await sandbox.set_tags.aio({**tags, "a13n_snapshot": image_id})
                await sandbox.terminate.aio(wait=True)
            await self.cleanup_snapshots()

    async def adopt_snapshot(self) -> None:
        """Record the image a stop that ended before its state was kept left on its terminated sandbox's tags."""
        import modal

        if self.target is None or self.target.snapshot_id is not None:
            return
        with sdk_errors():
            try:
                source = await modal.Sandbox.from_id.aio(self.target.target_id, client=await self.connection())
            except modal.exception.NotFoundError:
                return
            tags = await source.get_tags.aio()
        self.validate_labels(tags)
        if image_id := tags.get("a13n_snapshot"):
            self.remember(
                self.target.model_copy(update={"snapshot_id": image_id, "snapshot_source_id": source.object_id})
            )

    async def validate_snapshot(self, reference: SnapshotReference) -> None:
        import modal

        with sdk_errors():
            source = await modal.Sandbox.from_id.aio(reference.sandbox_id, client=await self.connection())
            tags = await source.get_tags.aio()
            self.validate_labels(tags)
            if tags.get("a13n_snapshot") != reference.image_id:
                raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)

    async def cleanup_snapshots(self) -> None:
        import modal
        import modal.experimental

        if self.target is None or not self.target.retired_snapshots:
            return
        client = await self.connection()
        for image in self.target.retired_snapshots:
            await self.validate_snapshot(image)
            with sdk_errors(mutation=True):
                try:
                    await modal.experimental.image_delete.aio(image.image_id, client=client)  # pyright: ignore[reportFunctionMemberAccess]
                except modal.exception.NotFoundError:
                    pass
            assert self.target
            self.remember(
                self.target.model_copy(
                    update={"retired_snapshots": tuple(item for item in self.target.retired_snapshots if item != image)}
                )
            )

    async def keepalive(self, *, deadline: datetime, operation_id: str) -> datetime:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            sandbox = await self.lookup()
            if sandbox is None:
                raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
            with sdk_errors():
                tags = await sandbox.get_tags.aio()
            try:
                expiry = datetime.fromisoformat(tags["a13n_expiry"])
            except (KeyError, ValueError):
                raise failure(self.provider_key, "provider_keepalive_unsupported", Category.UNSUPPORTED) from None
            if deadline.tzinfo is None or expiry.tzinfo is None or not operation_id or deadline > expiry:
                raise failure(self.provider_key, "provider_keepalive_limit", Category.UNSUPPORTED)
            return expiry

    async def destroy(self) -> None:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            sandbox = await self.lookup()
            if sandbox is not None:
                with sdk_errors(mutation=True):
                    await sandbox.terminate.aio(wait=True)
            if self.target and self.target.snapshot_id:
                self.remember(
                    self.target.model_copy(
                        update={
                            "retired_snapshots": (
                                *self.target.retired_snapshots,
                                SnapshotReference(
                                    image_id=self.target.snapshot_id,
                                    sandbox_id=self.target.snapshot_source_id or self.target.target_id,
                                ),
                            ),
                            "snapshot_id": None,
                            "snapshot_source_id": None,
                        }
                    )
                )
            await self.cleanup_snapshots()

    async def close_transport(self) -> None:
        try:
            if self.sandbox:
                await self.sandbox.detach.aio()
        finally:
            if self.client:
                await self.client.__aexit__(None, None, None)


MODAL = native_definition(
    type="modal",
    display_name="Modal",
    configuration_model=ModalConnectionConfiguration,
    credential_model=ModalCredential,
    environment_model=ModalEnvironmentConfiguration,
    state_model=ModalState,
    environment_type=ModalTarget,
    supports_stop=True,
    requires_keepalive=True,
)
