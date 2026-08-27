from __future__ import annotations

import asyncio
import base64
import binascii
import secrets
from dataclasses import dataclass
from typing import Protocol, Self

from a13n_envd_client.eip.v1 import (
    EIPCallContext,
    EIPClient,
    EncodedBytes,
    OutputInfo,
    OutputReadParams,
    OutputReference,
)
from a13n_envd_client.errors import EIPProtocolError


class _ProtocolCloser(Protocol):
    async def close_for_protocol_error(self, error: EIPProtocolError) -> None: ...


@dataclass(frozen=True, slots=True)
class EIPOutputPage:
    """One contiguous page returned by ``output.read``."""

    start_offset: int
    next_offset: int
    data: bytes
    output: OutputInfo
    eof: bool


class EIPOutputReader:
    """Stateful pager for one immutable EIP output reference."""

    def __init__(
        self,
        requester: _ProtocolCloser,
        client: EIPClient,
        reference: OutputReference,
        *,
        start_offset: int = 0,
        observed: OutputInfo | None = None,
    ) -> None:
        if not isinstance(start_offset, int) or isinstance(start_offset, bool) or start_offset < 0:
            raise ValueError("start_offset must be a non-negative integer")
        if observed is not None and observed.reference != reference:
            raise ValueError("observed output does not match the requested reference")
        if observed is not None and start_offset > observed.retained_bytes:
            raise ValueError("start_offset exceeds the observed retained output")
        self._requester = requester
        self._client = client
        self._reference = reference
        self._offset = start_offset
        self._observed = observed
        self._eof = bool(
            observed is not None and observed.producer_complete and start_offset == observed.retained_bytes
        )
        self._lock = asyncio.Lock()

    @property
    def reference(self) -> OutputReference:
        return self._reference

    @property
    def offset(self) -> int:
        return self._offset

    @property
    def output(self) -> OutputInfo | None:
        return self._observed

    @property
    def eof(self) -> bool:
        return self._eof

    async def read_page(self, *, wait_ms: int = 0) -> EIPOutputPage:
        if not isinstance(wait_ms, int) or isinstance(wait_ms, bool) or wait_ms < 0 or wait_ms > 2**64 - 1:
            raise ValueError("wait_ms must be a uint64 integer")
        async with self._lock:
            if self._eof:
                output = self._observed
                if output is None:
                    raise EIPProtocolError("output reader reached EOF without output evidence")
                return EIPOutputPage(
                    start_offset=self._offset,
                    next_offset=self._offset,
                    data=b"",
                    output=output,
                    eof=True,
                )
            requested_offset = self._offset
            result = await self._client.output_read(
                OutputReadParams(
                    context=EIPCallContext(operation_id=_operation_id()),
                    reference=self._reference,
                    start_offset=requested_offset,
                    wait_ms=wait_ms,
                )
            )
            try:
                data = _decode_bytes(result.data)
                self._validate_page(
                    requested_offset=requested_offset,
                    start_offset=result.start_offset,
                    next_offset=result.next_offset,
                    data=data,
                    output=result.output,
                )
            except EIPProtocolError as error:
                await self._requester.close_for_protocol_error(error)
                raise
            self._offset = result.next_offset
            self._observed = result.output
            self._eof = result.output.producer_complete and self._offset == result.output.retained_bytes
            return EIPOutputPage(
                start_offset=result.start_offset,
                next_offset=result.next_offset,
                data=data,
                output=result.output,
                eof=self._eof,
            )

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> bytes:
        while True:
            if self._eof:
                raise StopAsyncIteration
            page = await self.read_page(wait_ms=1_000)
            if page.data:
                return page.data
            if page.eof:
                raise StopAsyncIteration

    def _validate_page(
        self,
        *,
        requested_offset: int,
        start_offset: int,
        next_offset: int,
        data: bytes,
        output: OutputInfo,
    ) -> None:
        if output.reference != self._reference:
            raise EIPProtocolError("output.read changed the output reference")
        if start_offset != requested_offset:
            raise EIPProtocolError("output.read returned a non-contiguous start offset")
        if next_offset != start_offset + len(data):
            raise EIPProtocolError("output.read next offset does not match the page bytes")
        if next_offset > output.retained_bytes:
            raise EIPProtocolError("output.read page exceeds retained output")
        if not data and requested_offset < output.retained_bytes:
            raise EIPProtocolError("output.read made no progress before retained output end")
        if output.content_complete and not output.producer_complete:
            raise EIPProtocolError("output.read reported complete content from an active producer")
        previous = self._observed
        if previous is None:
            return
        if output.produced_bytes < previous.produced_bytes or output.retained_bytes < previous.retained_bytes:
            raise EIPProtocolError("output.read counters moved backwards")
        if previous.producer_complete and not output.producer_complete:
            raise EIPProtocolError("output.read producer completion moved backwards")
        if previous.content_complete and not output.content_complete:
            raise EIPProtocolError("output.read content completion moved backwards")
        if previous.producer_complete and (
            output.produced_bytes != previous.produced_bytes or output.retained_bytes != previous.retained_bytes
        ):
            raise EIPProtocolError("terminal output counters changed")
        previous_preview = _decode_bytes(previous.preview)
        current_preview = _decode_bytes(output.preview)
        if not current_preview.startswith(previous_preview):
            raise EIPProtocolError("output.read preview is not an immutable prefix")


def _decode_bytes(value: EncodedBytes) -> bytes:
    padding = "=" * (-len(value.data) % 4)
    try:
        return base64.b64decode(value.data + padding, validate=True)
    except (ValueError, binascii.Error) as error:  # generated validation should reject this first
        raise EIPProtocolError("output.read returned invalid base64 data") from error


def _operation_id() -> str:
    return f"op-{secrets.token_urlsafe(9)}"
