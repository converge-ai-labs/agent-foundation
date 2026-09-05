"""Raw-byte output references over sandbox-owned bounded capture files."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..commands import ProcessStatus
from ..models import EnvironmentError, EnvironmentOperationReceipt
from ..retention import (
    BoundOutputCursor,
    BoundOutputReference,
    EnvironmentOutputCapture,
    EnvironmentOutputPolicy,
    EnvironmentOutputReadResult,
    EnvironmentOutputSegment,
    OpaqueOutputCursor,
    OpaqueOutputReference,
    _unwrap_opaque,
)
from .commands import GuestCommands, decoded_bytes

_PROCESS_ID = re.compile(r"^process-[0-9a-f]{24}$")


class ProcessRecord(BaseModel):
    runner_pid: int = Field(gt=0)
    runner_start: str
    started_at: datetime
    ended_at: datetime | None
    phase: Literal["starting", "running", "exited", "signaled", "timed_out", "failed"]
    exit_code: int | None
    termination_reason: Literal["exit", "signal", "timeout", "output_limit", "backend_lost"] | None
    signal: Literal["interrupt", "terminate", "kill"] | None
    stdin_open: bool
    stdout_produced: int = Field(ge=0)
    stderr_produced: int = Field(ge=0)
    stdout_stored: int = Field(ge=0)
    stderr_stored: int = Field(ge=0)
    content_complete: bool
    cleanup: Literal["pending", "complete", "residual_confined", "failed"]

    @property
    def terminal(self) -> bool:
        return self.phase not in {"starting", "running"}

    @property
    def status(self) -> ProcessStatus:
        return ProcessStatus.model_validate(self.model_dump())


class E2BOutputs:
    def __init__(self, commands: GuestCommands) -> None:
        self.commands = commands

    async def record(self, process_id: str) -> ProcessRecord:
        validate_process_id(process_id)
        return ProcessRecord.model_validate(await self.commands.process("inspect", {"process_id": process_id}))

    def reference(self, process_id: str, stream: str) -> BoundOutputReference:
        return BoundOutputReference(
            mount_id=self.commands.mount_id,
            observed_generation=self.commands.generation,
            reference=OpaqueOutputReference._from_payload(f"{process_id}:{stream}"),
        )

    def _selector(self, reference: BoundOutputReference) -> tuple[str, Literal["stdout", "stderr"]]:
        self._scope(reference.mount_id, reference.observed_generation)
        value = _unwrap_opaque(reference.reference, OpaqueOutputReference)
        process_id, separator, stream = value.partition(":")
        validate_process_id(process_id)
        if not separator or stream not in {"stdout", "stderr"}:
            raise EnvironmentError("Invalid E2B output reference.", code="environment_request_invalid")
        return process_id, "stdout" if stream == "stdout" else "stderr"

    def _scope(self, mount_id: str, generation: str) -> None:
        if mount_id != self.commands.mount_id or generation != self.commands.generation:
            raise EnvironmentError("E2B reference is foreign or stale.", code="environment_stale_mount")

    async def capture(
        self,
        process_id: str,
        stream: Literal["stdout", "stderr"],
        record: ProcessRecord,
        policy: EnvironmentOutputPolicy,
        *,
        offset: int = 0,
    ) -> EnvironmentOutputCapture:
        size = record.stdout_stored if stream == "stdout" else record.stderr_stored
        produced = record.stdout_produced if stream == "stdout" else record.stderr_produced
        if offset < 0 or offset > size:
            raise EnvironmentError("E2B output offset is out of range.", code="environment_cursor_invalid")
        length = min(size - offset, policy.max_inline_bytes, 65536)
        data = decoded_bytes(
            await self.commands.process(
                "read", {"process_id": process_id, "stream": stream, "offset": offset, "length": length}
            )
        )
        if len(data) != length:
            raise EnvironmentError("E2B output changed during read.", code="environment_provider_failure")
        reference = self.reference(process_id, stream)
        end = offset + len(data)
        cursor = (
            None
            if record.terminal and end >= size
            else BoundOutputCursor(
                mount_id=self.commands.mount_id,
                observed_generation=self.commands.generation,
                cursor=OpaqueOutputCursor._from_payload(f"{process_id}:{stream}:{end}"),
            )
        )
        complete = record.content_complete and produced == size
        return EnvironmentOutputCapture(
            kind="retained",
            producer_complete=record.terminal,
            content_complete=complete,
            produced_bytes=produced,
            captured_bytes=size,
            dropped_bytes=produced - size,
            preview=(EnvironmentOutputSegment(start_offset=offset, data=data),) if data else (),
            reference=reference,
            cursor=cursor,
            available_end=size,
        )

    async def read(
        self,
        reference: BoundOutputReference,
        *,
        cursor: BoundOutputCursor | None = None,
        start_offset: int | None = None,
        policy: EnvironmentOutputPolicy,
    ) -> EnvironmentOutputReadResult:
        process_id, stream = self._selector(reference)
        if cursor is not None and start_offset is not None:
            raise EnvironmentError("Specify cursor or offset.", code="environment_request_invalid")
        offset = start_offset or 0
        if cursor is not None:
            self._scope(cursor.mount_id, cursor.observed_generation)
            raw = _unwrap_opaque(cursor.cursor, OpaqueOutputCursor)
            prefix, _, value = raw.rpartition(":")
            if prefix != f"{process_id}:{stream}" or not value.isdigit():
                raise EnvironmentError("Invalid E2B output cursor.", code="environment_cursor_invalid")
            offset = int(value)
        record = await self.record(process_id)
        capture = await self.capture(process_id, stream, record, policy, offset=offset)
        return EnvironmentOutputReadResult(chunks=capture.preview, next_cursor=capture.cursor, capture=capture)

    async def release(
        self, *, reference: BoundOutputReference | None = None, cursor: BoundOutputCursor | None = None
    ) -> EnvironmentOperationReceipt:
        if (reference is None) == (cursor is None):
            raise EnvironmentError("Release requires one reference or cursor.", code="environment_request_invalid")
        if reference is None:
            assert cursor is not None
            self._scope(cursor.mount_id, cursor.observed_generation)
            prefix, _, offset = _unwrap_opaque(cursor.cursor, OpaqueOutputCursor).rpartition(":")
            if not offset.isdigit():
                raise EnvironmentError("Invalid E2B output cursor.", code="environment_cursor_invalid")
            reference = BoundOutputReference(
                mount_id=cursor.mount_id,
                observed_generation=cursor.observed_generation,
                reference=OpaqueOutputReference._from_payload(prefix),
            )
        process_id, stream = self._selector(reference)
        await self.commands.process("release_output", {"process_id": process_id, "stream": stream}, mutation=True)
        return self.commands.receipt()


def validate_process_id(value: str) -> None:
    if not _PROCESS_ID.fullmatch(value):
        raise EnvironmentError("Invalid E2B process identity.", code="environment_request_invalid")
