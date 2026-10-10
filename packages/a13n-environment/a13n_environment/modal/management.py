"""Modal management implementation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ..errors import EnvironmentProviderErrorCategory as Category
from ..models import EnvironmentState
from ..native.environment import NativeManagement, NativeRuntime
from ..native.errors import failure
from .shared import (
    ModalEnvironmentConfiguration,
    ModalReference,
    ModalState,
    SnapshotReference,
    execute_command,
    sdk_errors,
)

if TYPE_CHECKING:
    import modal


class ModalManagement(ModalReference, NativeManagement[ModalEnvironmentConfiguration, ModalState]):
    def __init__(
        self,
        config: ModalEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
        operation_id: str,
    ):
        super().__init__(config, environment_id, state, runtime)
        del operation_id

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
            self.observe(self.target.model_copy(update={"target_id": sandbox.object_id}))
        else:
            self.observe(
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
        await execute_command(sandbox, [self.config.python, "-I", "-c", "pass"], self.config.request_timeout_seconds)
        if self.target and self.target.snapshot_id:
            self.observe(
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
                    self.observe(
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
            self.observe(
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
            self.observe(
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
                self.observe(
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
