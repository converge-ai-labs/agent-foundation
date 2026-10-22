"""Modal management implementation."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentError, EnvironmentState
from a13n_harness.providers.environment.modal.diagnostics import measure
from a13n_harness.providers.environment.modal.http import HttpReference, http_execution, tunnel_connection
from a13n_harness.providers.environment.modal.shared import (
    ModalEnvironmentConfiguration,
    ModalReference,
    ModalState,
    SnapshotReference,
    execute_command,
    sdk_errors,
)
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.errors import failure

from a13n_service.providers.environment import desktop_access as access
from a13n_service.providers.environment.native import NativeManagement

from .configuration import ModalRecipe

if TYPE_CHECKING:
    import modal


class ModalManagement(ModalReference, NativeManagement[ModalEnvironmentConfiguration, ModalState]):
    def __init__(
        self,
        configuration: ModalRecipe,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime | None,
        operation_id: str,
    ):
        if runtime is None:
            raise TypeError("Modal management requires a runtime")
        super().__init__(configuration, environment_id, state, runtime)
        self.recipe = configuration
        self._desktop_sandbox: modal.Sandbox | None = None
        del operation_id

    async def _allocate(self, snapshot: SnapshotReference | None) -> modal.Sandbox:
        import modal

        client = await self.connection()
        if snapshot is not None:
            await self.validate_snapshot(snapshot)
        with sdk_errors():
            try:
                app = await modal.App.lookup.aio(
                    self.backend.app_name, environment_name=self.backend.environment_name, client=client
                )
            except modal.exception.NotFoundError:
                raise failure(self.provider_key, "provider_target_missing", Category.MISSING) from None
        with sdk_errors(mutation=True):
            image = (
                modal.Image.from_id(snapshot.image_id if snapshot else self.config.image, client=client)
                if snapshot or re.fullmatch(r"im-[A-Za-z0-9]+", self.config.image)
                else modal.Image.from_registry(self.config.image)
            )
            expiry = datetime.now(UTC) + timedelta(seconds=self.config.timeout_seconds)
            tags = {**self.labels, "a13n_expiry": expiry.isoformat()}
            if snapshot is not None:
                tags["a13n_resume_snapshot"] = snapshot.image_id
            sandbox = await modal.Sandbox.create.aio(
                # Filesystem snapshots can retain runtime paths, not their owners.
                # Only a newly allocated VM clears these before starting services.
                "sh",
                "-c",
                "rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 /run/a13n-desktop-access/control.sock; "
                "exec a13n-sandbox-entrypoint " + ("a13n-sandbox-desktop" if self.config.desktop else "a13n-envd"),
                app=app,
                name=self.name,
                image=image,
                client=client,
                tags=tags,
                timeout=self.config.timeout_seconds,
                cpu=self.config.cpu,
                memory=self.config.memory,
                workdir="/workspace",
                encrypted_ports=[8888, *([access.PORT] if self.recipe.desktop_assistance else [])],
                experimental_options={"vm_runtime": True} if self.config.desktop else None,
                env={
                    "A13N_ENVD_DEVICE_ID": self.owner,
                    "A13N_ENVD_TRANSPORT": "http",
                    "A13N_ENVD_HTTP_BIND": "0.0.0.0:8888",
                    "A13N_ENVD_HTTP_PLAINTEXT_SCOPE": "provider_private_link",
                    "A13N_ENVD_EXECUTION_UID": "1000",
                    "A13N_ENVD_EXECUTION_GID": "1000",
                    "A13N_ENVD_FULL_CONTROL": "true",
                    "A13N_ENVD_STATE_DIR": "/var/lib/a13n-envd",
                    "A13N_ENVD_RUNTIME_DIR": "/run/a13n-envd-state",
                    "A13N_DESKTOP_ACCESS": "true" if self.recipe.desktop_assistance else "false",
                },
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
        await self._check_envd()
        if self.recipe.desktop_assistance:
            await self.desktop_access(access.Describe())
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

    async def _check_envd(self) -> None:
        assert self.sandbox is not None
        probe = """import socket,time
end=time.monotonic()+30
while time.monotonic()<end:
 try:
  with socket.create_connection(('127.0.0.1',8888),timeout=1): break
 except OSError: time.sleep(.2)
else: raise RuntimeError('a13n-envd did not become ready')
"""
        await execute_command(self.sandbox, ["python3", "-I", "-c", probe], 35)
        runtime = NativeRuntime(self.backend, self.credential)
        async with asyncio.timeout(self.config.request_timeout_seconds):
            while True:
                execution = http_execution(HttpReference(self.config, self.environment_id, self.state, runtime))
                try:
                    await execution.open(execution_id="exec-readiness")
                    await execution.check_ready(frozenset({"computer"}) if self.config.desktop else frozenset())
                    if not self.config.desktop:
                        return
                    computer = execution.operations.computer
                    if computer is not None:
                        description = await computer.describe()
                        if description.observe_ready and description.input_ready and description.targets:
                            return
                except EnvironmentProviderError as error:
                    if error.category not in {Category.UNAVAILABLE, Category.TIMEOUT}:
                        raise
                except EnvironmentError as error:
                    if error.code not in {"environment_unavailable", "environment_provider_failure"}:
                        raise
                finally:
                    await execution.close()
                await asyncio.sleep(0.1)

    async def desktop_access(self, request: access.Request) -> access.Reply:
        self.reuse_transport = True
        try:
            if not self.recipe.desktop_assistance or not self.config.desktop or self.target is None:
                raise failure(self.provider_key, "desktop_access_unavailable", Category.UNSUPPORTED)
            sandbox = self._desktop_sandbox
            if sandbox is None:
                sandbox = await self.lookup()
                if sandbox is None:
                    raise failure(self.provider_key, "desktop_target_unavailable", Category.CONFLICT)
                # The authorization scope pins this validated target. Each guest
                # command still enforces generation/epoch; never rediscover a replacement.
                self._desktop_sandbox = self.sandbox = sandbox
            reply = access.decode(
                self.provider_key, request, await execute_command(sandbox, access.command(request), 25)
            )
            if isinstance(reply, access.Description):
                with measure("sandbox_tunnels"), sdk_errors():
                    tunnels = await sandbox.tunnels.aio(timeout=int(self.config.request_timeout_seconds))
                tunnel = tunnels.get(access.PORT)
                if tunnel is None:
                    raise failure(self.provider_key, "desktop_access_unavailable", Category.UNSUPPORTED)
                # The TLS tunnel carries the per-grant supervisor credentials only.
                # Never send Modal account or Envd credentials to the browser.
                connection = tunnel_connection(tunnel.url)
                return reply.model_copy(
                    update={"websocket_url": connection.endpoint.replace("https://", "wss://", 1) + "/websockify"}
                )
            return reply
        except BaseException:
            self.discard_transport()
            raise

    async def create(self) -> None:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            await self.invalidate_cached_transport()
            sandbox = await self.lookup()
            if sandbox is None:
                if self.target is not None:
                    raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
                sandbox = await self._allocate(None)
            await self._finish_start(sandbox)

    async def start(self) -> None:
        import modal

        async with asyncio.timeout(self.config.request_timeout_seconds):
            await self.invalidate_cached_transport()
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
            await self.invalidate_cached_transport()
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
            await self.invalidate_cached_transport()
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
