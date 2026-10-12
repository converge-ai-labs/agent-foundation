"""Modal execution implementation."""

from __future__ import annotations

import asyncio

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.modal.shared import (
    ModalEnvironmentConfiguration,
    ModalReference,
    ModalState,
    execute_command,
)
from a13n_harness.providers.environment.native.environment import NativeExecution
from a13n_harness.providers.environment.native.errors import failure


class ModalExecution(ModalReference, NativeExecution[ModalEnvironmentConfiguration, ModalState]):
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
        return await execute_command(self.sandbox, argv, timeout)
