"""Control file transfers, multiplexed on the connection's existing relay reader."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections import OrderedDict
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Literal

from a13n_harness.providers.environment.files import FileOperator, FileWriteMode
from a13n_harness.providers.environment.models import EnvironmentError
from pydantic import Field, JsonValue

from a13n_service.ids import ObjectId

from ..domain import DomainModel
from .relay_flow import TransferWindow
from .relay_protocol import RelayChunk, RelayCredit, RelayFinish, RelayInput, RelayRequest, TransferPosition
from .relay_storage import ConnectionRelayStore


class ReadBytes(DomainModel):
    operation: Literal["file.read_bytes"] = "file.read_bytes"
    transfer_id: ObjectId
    path: str
    offset: int = Field(default=0, ge=0)
    length: int | None = Field(default=None, ge=0)


class WriteBytes(DomainModel):
    operation: Literal["file.write_bytes"] = "file.write_bytes"
    transfer_id: ObjectId
    path: str
    mode: FileWriteMode


@dataclass(frozen=True, slots=True)
class FileTransferPlan:
    files: FileOperator
    parameters: ReadBytes | WriteBytes

    def bind(self, store: ConnectionRelayStore, request: RelayRequest, entry: str) -> FileTransferExecution:
        return FileTransferExecution(self, store, request, entry)


class FileTransferExecution:
    def __init__(self, plan: FileTransferPlan, store: ConnectionRelayStore, request: RelayRequest, entry: str) -> None:
        self._plan, self._store, self._request, self._entry = plan, store, request, entry
        self._window = TransferWindow(plan.parameters.transfer_id, store.limits.input_window)
        self.position = self._window.position
        self._received = self.position
        self._inputs: asyncio.Queue[tuple[TransferPosition, bytes] | RelayFinish] = asyncio.Queue(
            store.limits.input_window + 1
        )
        self._history: OrderedDict[int, bytes] = OrderedDict()
        self._finished = False
        self.failure: EnvironmentError | None = None

    def accept(self, frame: RelayInput) -> None:
        if isinstance(self._plan.parameters, ReadBytes):
            if not isinstance(frame, RelayCredit):
                raise self._invalid()
            self._window.credit(frame.transfer)
            return
        if isinstance(frame, RelayCredit):
            raise self._invalid()
        digest = hashlib.sha256(frame.model_dump_json().encode()).digest()
        if frame.transfer.sequence < self._received.sequence or self._finished:
            if self._history.get(frame.transfer.sequence) != digest:
                raise self._invalid()
            return
        if frame.transfer != self._received:
            raise self._invalid()
        if isinstance(frame, RelayFinish):
            self._finished = True
            item = frame
        else:
            data = base64.b64decode(frame.data, validate=True)
            if not 0 < len(data) <= self._store.limits.chunk_bytes:
                raise self._invalid()
            self._received = TransferPosition(
                transfer_id=frame.transfer.transfer_id,
                sequence=frame.transfer.sequence + 1,
                offset=frame.transfer.offset + len(data),
            )
            item = (self._received, data)
        try:
            self._inputs.put_nowait(item)
        except asyncio.QueueFull as error:
            raise self._invalid() from error
        self._history[frame.transfer.sequence] = digest
        if len(self._history) > 128:
            self._history.popitem(last=False)

    @staticmethod
    def _invalid() -> EnvironmentError:
        return EnvironmentError(
            "File transfer input is incomplete or out of order", code="environment_transfer_incomplete"
        )

    async def __call__(self) -> JsonValue:
        parameters = self._plan.parameters
        if isinstance(parameters, ReadBytes):
            await self._download(parameters)
            return None
        result = await self._plan.files.write_bytes_stream(parameters.path, self._upload(), mode=parameters.mode)
        if not self._finished or self.position != self._received or not self._inputs.empty():
            raise self._invalid()
        return result.model_dump(mode="json")

    async def _download(self, parameters: ReadBytes) -> None:
        skip, remaining = parameters.offset, parameters.length
        if remaining == 0:
            return
        stream = self._plan.files.read_bytes_stream(parameters.path, chunk_size=self._store.limits.chunk_bytes)
        try:
            async for data in stream:
                if skip:
                    skipped = min(skip, len(data))
                    data, skip = data[skipped:], skip - skipped
                if remaining is not None:
                    data = data[:remaining]
                    remaining -= len(data)
                for index in range(0, len(data), self._store.limits.chunk_bytes):
                    chunk = data[index : index + self._store.limits.chunk_bytes]
                    await self._window.wait_slot()
                    position = self._window.sent(len(chunk))
                    await self._store.chunk(
                        self._request,
                        self._entry,
                        RelayChunk(
                            request_id=self._request.request_id,
                            scope=self._request.scope,
                            transfer=position,
                            data=base64.b64encode(chunk).decode(),
                        ),
                    )
                    self.position = self._window.position
                if remaining == 0:
                    break
        finally:
            if isinstance(stream, AsyncGenerator):
                await stream.aclose()

    async def _upload(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._inputs.get()
            if isinstance(item, RelayFinish):
                if item.transfer != self.position:
                    raise self._invalid()
                return
            position, data = item
            yield data
            # Returning for the next chunk means the provider accepted the
            # previous one. This grants transport credit, never a mutation receipt.
            await self._store.credit(
                self._request,
                self._entry,
                RelayCredit(request_id=self._request.request_id, scope=self._request.scope, transfer=position),
            )
            self.position = position
