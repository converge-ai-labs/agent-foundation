"""Runloop execution implementation."""

import shlex
from collections.abc import AsyncIterator
from typing import BinaryIO

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.providers.environment.native.configuration import TargetState
from a13n_harness.providers.environment.native.environment import NativeExecution
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import decode_response
from a13n_harness.providers.environment.native.transfers import CHUNK_BYTES, multipart_file
from a13n_harness.providers.environment.runloop.shared import (
    Execution,
    RunloopEnvironmentConfiguration,
    RunloopReference,
)


class RunloopExecution(RunloopReference, NativeExecution[RunloopEnvironmentConfiguration, TargetState]):
    async def open(self, *, execution_id: str) -> None:
        self.require_target()
        target = await self.lookup()
        if target is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        if target.status != "running":
            raise failure(self.provider_key, "provider_target_stopped", Category.CONFLICT)
        await self.open_operations(self.execute, target.id, execution_id)

    async def read_stream(self, path: str) -> AsyncIterator[bytes]:
        assert self.target
        async with self.transport().stream(
            "POST",
            f"/v1/devboxes/{self.target.target_id}/download_file",
            body={"path": path},
            headers={"Accept": "application/octet-stream"},
            mutation=False,
        ) as response:
            async for chunk in response.aiter_bytes(CHUNK_BYTES):
                yield chunk

    async def write_stream(self, path: str, source: BinaryIO, *, mode: int) -> None:
        del mode  # Existing guest-created candidate retains its permissions.
        assert self.target
        content_type, content = multipart_file(path, source)
        async with self.transport().stream(
            "POST",
            f"/v1/devboxes/{self.target.target_id}/upload_file",
            headers={"Content-Type": content_type},
            content=content,
        ):
            pass

    async def execute(self, argv: list[str], timeout: float) -> str:
        assert self.target
        result = decode_response(
            Execution,
            await self.transport().request(
                "POST", f"/v1/devboxes/{self.target.target_id}/execute_sync", body={"command": shlex.join(argv)}
            ),
            self.provider_key,
            mutation=True,
        )
        if result.exit_status != 0:
            raise failure(
                self.provider_key, "provider_command_failed", Category.PROVIDER_FAILURE, certainty=Certainty.KNOWN
            )
        return result.stdout
