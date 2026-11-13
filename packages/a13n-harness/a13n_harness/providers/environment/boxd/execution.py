"""boxd execution implementation."""

import asyncio
from collections.abc import AsyncIterator
from typing import BinaryIO

from a13n_harness.environment._streams import closing_stream
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.providers.environment.boxd.shared import BoxdEnvironmentConfiguration, BoxdReference, sdk_errors
from a13n_harness.providers.environment.native.configuration import TargetState
from a13n_harness.providers.environment.native.environment import NativeExecution
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.transfers import file_chunks

# The guest helper bounds the command and then needs a few seconds to end its process group.
# The call's own deadline sits well above that, so it never ends a command the helper still owns.
EXEC_DEADLINE_MARGIN_SECONDS = 30


class BoxdExecution(BoxdReference, NativeExecution[BoxdEnvironmentConfiguration, TargetState]):
    async def open(self, *, execution_id: str) -> None:
        self.require_target()
        machine = await self.lookup()
        if machine is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        # A hibernated or stopped machine stays stopped until an explicit management start.
        if self.phase(machine) != "running":
            raise failure(self.provider_key, "provider_target_stopped", Category.CONFLICT)
        await self.open_operations(self.execute, machine.id, execution_id)

    async def read_stream(self, path: str) -> AsyncIterator[bytes]:
        assert self.target
        # One deadline bounds the whole download, across every wait for content.
        deadline = asyncio.get_running_loop().time() + self.config.request_timeout_seconds
        with sdk_errors(mutation=False):
            # Close the download stream as soon as a bounded range read stops consuming it.
            async with closing_stream(self.client().machines.fs.read_stream(self.target.target_id, path)) as stream:
                chunks = aiter(stream)
                while True:
                    try:
                        async with asyncio.timeout_at(deadline):
                            chunk = await anext(chunks)
                    except StopAsyncIteration:
                        break
                    yield chunk

    async def write_stream(self, path: str, source: BinaryIO, *, mode: int) -> None:
        assert self.target
        with sdk_errors(mutation=True):
            await self.client().machines.fs.write(self.target.target_id, path, file_chunks(source), mode=mode)

    async def execute(self, argv: list[str], timeout: float) -> str:
        assert self.target
        with sdk_errors(mutation=True):
            result = await self.client().machines.exec(
                self.target.target_id, argv, timeout=timeout + EXEC_DEADLINE_MARGIN_SECONDS
            )
        if result.exit_code < 0:
            # The stream ended without an exit status: the command may or may not have run.
            raise failure(
                self.provider_key, "provider_unknown_outcome", Category.UNKNOWN_OUTCOME, certainty=Certainty.UNKNOWN
            )
        if result.exit_code != 0:
            raise failure(
                self.provider_key, "provider_command_failed", Category.PROVIDER_FAILURE, certainty=Certainty.KNOWN
            )
        return str(result.stdout)
