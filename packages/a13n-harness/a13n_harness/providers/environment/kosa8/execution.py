"""kosa8 execution implementation: streamed exec and the raw files API; never replay a dispatched command."""

import asyncio
import base64
import binascii
import json
from collections.abc import AsyncIterator
from typing import BinaryIO

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.providers.environment.kosa8.shared import (
    PROVIDER_KEY,
    Kosa8EnvironmentConfiguration,
    Kosa8Reference,
)
from a13n_harness.providers.environment.native.configuration import TargetState
from a13n_harness.providers.environment.native.environment import NativeExecution
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.transfers import CHUNK_BYTES, file_chunks

# The guest helper bounds the command and then needs a few seconds to end its process group.
# The call's own deadline sits above that, so it never ends a command the helper still owns.
EXEC_DEADLINE_MARGIN_SECONDS = 30
# kosa8 takes at most 256 argv entries of 8192 UTF-16 units each; 4096 code points always fit.
MAX_ARGV_ENTRIES = 256
ARGV_CHUNK = 4096
# Rejoins argv split into bounded pieces: $1 lists each entry's piece count, the pieces follow.
_REJOIN = 'c=$1; shift; a=(); for n in ${c//,/ }; do v=; for ((i=0; i<n; i++)); do v+=$1; shift; done; a+=("$v"); done; exec "${a[@]}"'


def bounded_argv(argv: list[str]) -> list[str]:
    """argv within kosa8's limits: unchanged when it fits, otherwise split and rejoined by bash in the guest."""
    if len(argv) <= MAX_ARGV_ENTRIES and all(len(entry) <= ARGV_CHUNK for entry in argv):
        return argv
    pieces = [[entry[i : i + ARGV_CHUNK] for i in range(0, len(entry), ARGV_CHUNK)] for entry in argv]
    rejoined = ["/bin/bash", "-c", _REJOIN, "a13n-kosa8", ",".join(str(len(p)) for p in pieces)]
    rejoined.extend(piece for entry in pieces for piece in entry)
    if len(rejoined) > MAX_ARGV_ENTRIES:
        raise failure(PROVIDER_KEY, "provider_spec_invalid", Category.INVALID, certainty=Certainty.KNOWN)
    return rejoined


class Kosa8Execution(Kosa8Reference, NativeExecution[Kosa8EnvironmentConfiguration, TargetState]):
    async def open(self, *, execution_id: str) -> None:
        self.require_target()
        sandbox = await self.lookup()
        if sandbox is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        # A sleeping sandbox stays stopped until an explicit management start.
        if self.phase(sandbox) != "running":
            raise failure(self.provider_key, "provider_target_stopped", Category.CONFLICT)
        await self.open_operations(self.execute, sandbox.id, execution_id)

    async def read_stream(self, path: str) -> AsyncIterator[bytes]:
        assert self.target
        async with self.transport().stream(
            "GET",
            f"/v1/sandboxes/{self.target.target_id}/files/raw",
            params={"path": path},
            headers={"Accept": "application/octet-stream"},
        ) as response:
            async for chunk in response.aiter_bytes(CHUNK_BYTES):
                yield chunk

    async def write_stream(self, path: str, source: BinaryIO, *, mode: int) -> None:
        del mode  # The existing guest-created candidate retains its permissions.
        assert self.target
        async with self.transport().stream(
            "PUT",
            f"/v1/sandboxes/{self.target.target_id}/files/raw",
            params={"path": path},
            headers={"Content-Type": "application/octet-stream"},
            content=file_chunks(source),
        ):
            pass

    async def execute(self, argv: list[str], timeout: float) -> str:
        assert self.target
        output = bytearray()
        try:
            async with asyncio.timeout(timeout + EXEC_DEADLINE_MARGIN_SECONDS):
                async with self.transport().stream(
                    "POST",
                    f"/v1/sandboxes/{self.target.target_id}/exec",
                    body={"cmd": bounded_argv(argv), "stream": True},
                ) as response:
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        frame = json.loads(line)
                        if frame.get("done") is True:
                            code = frame.get("exit")
                            if "error" in frame or type(code) is not int:
                                break
                            if code != 0:
                                raise failure(
                                    self.provider_key,
                                    "provider_command_failed",
                                    Category.PROVIDER_FAILURE,
                                    certainty=Certainty.KNOWN,
                                )
                            return output.decode()
                        if frame.get("stream") == "stdout" and not frame.get("heartbeat"):
                            output.extend(base64.b64decode(frame["data"], validate=True))
                            if len(output) > 24 * 1024 * 1024:
                                break
        except (ValueError, TypeError, KeyError, TimeoutError, binascii.Error):
            pass
        # The stream ended without an exit status: the command may or may not have run.
        raise failure(
            self.provider_key, "provider_unknown_outcome", Category.UNKNOWN_OUTCOME, certainty=Certainty.UNKNOWN
        )
