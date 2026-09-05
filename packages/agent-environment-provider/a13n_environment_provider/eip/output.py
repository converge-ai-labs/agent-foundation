from __future__ import annotations

import secrets
from dataclasses import dataclass

from a13n_envd_client import EIPSession
from a13n_envd_client.eip import v1 as eip

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
from ._common import convert_receipt, decode_bytes, new_context, raise_converted, seconds_to_milliseconds


@dataclass(slots=True)
class _OutputRecord:
    reference: eip.OutputReference
    max_output_bytes: int
    observed: eip.OutputInfo


@dataclass(frozen=True, slots=True)
class _CursorRecord:
    output_token: str
    offset: int
    projection_start: int
    projection_end: int


class EIPOutputRegistry:
    def __init__(
        self,
        *,
        session: EIPSession,
        environment_id: str,
        mount_id: str,
        generation: str,
    ) -> None:
        self._session = session
        self._environment_id = environment_id
        self._mount_id = mount_id
        self._generation = generation
        self._records: dict[str, _OutputRecord] = {}
        self._raw_tokens: dict[eip.OutputReference, str] = {}
        self._cursors: dict[str, _CursorRecord] = {}
        self._pending_cleanup: set[eip.OutputReference] = set()

    def capture(
        self,
        output: eip.OutputInfo,
        *,
        policy: EnvironmentOutputPolicy,
    ) -> EnvironmentOutputCapture:
        token = self._raw_tokens.get(output.reference)
        if token is None:
            token = self._token("output")
            self._raw_tokens[output.reference] = token
            self._records[token] = _OutputRecord(
                reference=output.reference,
                max_output_bytes=policy.max_output_bytes,
                observed=output,
            )
        else:
            self._records[token].observed = output
        return self._capture(token, policy)

    async def read(
        self,
        reference: BoundOutputReference,
        *,
        cursor: BoundOutputCursor | None = None,
        start_offset: int | None = None,
        policy: EnvironmentOutputPolicy,
        wait_seconds: float = 0,
    ) -> EnvironmentOutputReadResult:
        if cursor is not None and start_offset is not None:
            raise EnvironmentError(
                "Specify an output cursor or start offset, not both",
                code="environment_request_invalid",
            )
        output_token = self._validate_reference(reference)
        record = self._records.get(output_token)
        if record is None:
            raise EnvironmentError("Retained output is unavailable", code="environment_not_found")
        if cursor is None:
            offset = 0 if start_offset is None else start_offset
            if offset < 0:
                raise EnvironmentError("Output offset is invalid", code="environment_request_invalid")
            projection_start = offset
            projection_end = min(offset + policy.max_output_bytes, record.max_output_bytes)
        else:
            cursor_record = self._validate_cursor(cursor, output_token)
            offset = cursor_record.offset
            projection_start = cursor_record.projection_start
            projection_end = min(
                cursor_record.projection_end,
                projection_start + policy.max_output_bytes,
            )
        allowed = min(policy.max_inline_bytes, max(projection_end - offset, 0))
        if allowed == 0:
            return EnvironmentOutputReadResult(
                chunks=(),
                next_cursor=None,
                capture=self._capture(output_token, policy),
            )
        try:
            reader = self._session.open_output(
                record.reference,
                start_offset=offset,
                observed=record.observed,
            )
            page = await reader.read_page(wait_ms=0 if wait_seconds == 0 else seconds_to_milliseconds(wait_seconds))
        except BaseException as error:
            raise_converted(error)
        record.observed = page.output
        data = page.data[:allowed]
        returned_end = page.start_offset + len(data)
        terminal = page.output.producer_complete and returned_end >= page.output.retained_bytes
        next_cursor = None
        if returned_end < projection_end and not terminal:
            cursor_token = self._token("cursor")
            self._cursors[cursor_token] = _CursorRecord(
                output_token=output_token,
                offset=returned_end,
                projection_start=projection_start,
                projection_end=projection_end,
            )
            next_cursor = BoundOutputCursor(
                mount_id=self._mount_id,
                observed_generation=self._generation,
                cursor=OpaqueOutputCursor._from_payload(cursor_token),
            )
        chunks = (EnvironmentOutputSegment(start_offset=page.start_offset, data=data),) if data else ()
        return EnvironmentOutputReadResult(
            chunks=chunks,
            next_cursor=next_cursor,
            capture=self._capture(output_token, policy, cursor=next_cursor),
        )

    async def release(
        self,
        *,
        reference: BoundOutputReference | None = None,
        cursor: BoundOutputCursor | None = None,
    ) -> EnvironmentOperationReceipt:
        if (reference is None) == (cursor is None):
            raise EnvironmentError(
                "Release requires exactly one output reference or cursor",
                code="environment_request_invalid",
            )
        if reference is not None:
            output_token = self._validate_reference(reference)
        else:
            assert cursor is not None
            cursor_token = self._validate_bound_cursor(cursor)
            cursor_record = self._cursors.get(cursor_token)
            if cursor_record is None:
                raise EnvironmentError("Output cursor is invalid", code="environment_cursor_invalid")
            output_token = cursor_record.output_token
        record = self._records.get(output_token)
        if record is None:
            raise EnvironmentError("Retained output is unavailable", code="environment_not_found")
        self._pending_cleanup.add(record.reference)
        return await self._release_record(output_token, record)

    def defer_cleanup(self, reference: eip.OutputReference) -> None:
        self._pending_cleanup.add(reference)

    async def release_hidden(self, reference: eip.OutputReference) -> None:
        self.defer_cleanup(reference)
        try:
            await self.release_raw(reference)
        except EnvironmentError as exc:
            if exc.code != "environment_not_found":
                raise
            self._forget_reference(reference)

    def _forget_reference(self, reference: eip.OutputReference) -> None:
        output_token = self._raw_tokens.pop(reference, None)
        self._pending_cleanup.discard(reference)
        if output_token is None:
            return
        self._records.pop(output_token, None)
        self._cursors = {token: item for token, item in self._cursors.items() if item.output_token != output_token}

    async def release_raw(self, reference: eip.OutputReference) -> EnvironmentOperationReceipt:
        output_token = self._raw_tokens.get(reference)
        if output_token is None:
            released, receipt = await self._release_reference(reference)
            if released:
                self._pending_cleanup.discard(reference)
            return receipt
        return await self._release_record(output_token, self._records[output_token])

    async def cleanup_pending(self) -> None:
        first_error: EnvironmentError | None = None
        for reference in tuple(self._pending_cleanup):
            try:
                await self.release_raw(reference)
            except EnvironmentError as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    async def _release_record(
        self,
        output_token: str,
        record: _OutputRecord,
    ) -> EnvironmentOperationReceipt:
        released, receipt = await self._release_reference(record.reference)
        if released:
            self._records.pop(output_token, None)
            self._raw_tokens.pop(record.reference, None)
            self._pending_cleanup.discard(record.reference)
            self._cursors = {token: item for token, item in self._cursors.items() if item.output_token != output_token}
        return receipt

    async def _release_reference(
        self,
        reference: eip.OutputReference,
    ) -> tuple[bool, EnvironmentOperationReceipt]:
        try:
            result = await self._session.client.output_release(
                eip.OutputReleaseParams(context=new_context(), reference=reference)
            )
        except BaseException as error:
            raise_converted(error)
        receipt = convert_receipt(
            result.receipt,
            environment_id=self._session.descriptor.environment_id,
            mount_id=self._mount_id,
            generation=self._generation,
        )
        return result.released, receipt

    def _capture(
        self,
        token: str,
        policy: EnvironmentOutputPolicy,
        *,
        cursor: BoundOutputCursor | None = None,
    ) -> EnvironmentOutputCapture:
        record = self._records[token]
        output = record.observed
        if policy.overflow == "fail" and output.producer_complete and output.produced_bytes > record.max_output_bytes:
            raise EnvironmentError("Command output exceeds the model projection limit", code="environment_too_large")
        preview = decode_bytes(output.preview)[: min(policy.max_inline_bytes, record.max_output_bytes)]
        fully_inline = (
            output.producer_complete
            and output.content_complete
            and output.retained_bytes <= policy.max_inline_bytes
            and output.retained_bytes <= record.max_output_bytes
            and len(preview) == output.retained_bytes
        )
        reference = None
        if output.producer_complete and output.retained_bytes == 0:
            kind = "empty"
        elif fully_inline:
            kind = "inline"
        elif policy.overflow == "retain":
            kind = "retained"
            reference = BoundOutputReference(
                mount_id=self._mount_id,
                observed_generation=self._generation,
                reference=OpaqueOutputReference._from_payload(token),
            )
        else:
            kind = "truncated"
        return EnvironmentOutputCapture(
            kind=kind,
            producer_complete=output.producer_complete,
            content_complete=output.content_complete,
            produced_bytes=output.produced_bytes,
            captured_bytes=output.retained_bytes,
            dropped_bytes=max(output.produced_bytes - output.retained_bytes, 0),
            inline=preview if kind in {"inline", "truncated"} else None,
            preview=(EnvironmentOutputSegment(start_offset=0, data=preview),) if preview else (),
            reference=reference,
            cursor=cursor,
            available_start=0,
            available_end=output.retained_bytes,
            expires_at=None,
        )

    def _validate_reference(self, reference: BoundOutputReference) -> str:
        self._validate_bound_identity(reference.mount_id, reference.observed_generation)
        return _unwrap_opaque(reference.reference, OpaqueOutputReference)

    def _validate_bound_cursor(self, cursor: BoundOutputCursor) -> str:
        self._validate_bound_identity(cursor.mount_id, cursor.observed_generation)
        return _unwrap_opaque(cursor.cursor, OpaqueOutputCursor)

    def _validate_cursor(self, cursor: BoundOutputCursor, output_token: str) -> _CursorRecord:
        token = self._validate_bound_cursor(cursor)
        record = self._cursors.get(token)
        if record is None or record.output_token != output_token:
            raise EnvironmentError("Output cursor is invalid", code="environment_cursor_invalid")
        return record

    def _validate_bound_identity(self, mount_id: str, generation: str) -> None:
        if mount_id != self._mount_id or generation != self._generation:
            raise EnvironmentError("Output selector is foreign or stale", code="environment_stale_mount")

    @staticmethod
    def _token(prefix: str) -> str:
        return f"{prefix}-{secrets.token_urlsafe(12)}"


class EIPOutputOperations:
    def __init__(self, registry: EIPOutputRegistry) -> None:
        self._registry = registry

    async def read(
        self,
        reference: BoundOutputReference,
        *,
        cursor: BoundOutputCursor | None = None,
        start_offset: int | None = None,
        policy: EnvironmentOutputPolicy,
    ) -> EnvironmentOutputReadResult:
        return await self._registry.read(
            reference,
            cursor=cursor,
            start_offset=start_offset,
            policy=policy,
        )

    async def release(
        self,
        *,
        reference: BoundOutputReference | None = None,
        cursor: BoundOutputCursor | None = None,
    ) -> EnvironmentOperationReceipt:
        return await self._registry.release(reference=reference, cursor=cursor)
