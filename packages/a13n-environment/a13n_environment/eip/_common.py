from __future__ import annotations

import asyncio
import base64
import math
import secrets
from collections.abc import Awaitable
from datetime import datetime
from typing import Never

from a13n_envd_client import EIPSession
from a13n_envd_client.eip import v1 as eip
from a13n_envd_client.errors import (
    EIPMethodError,
    EIPProtocolError,
    EIPSessionStateError,
    EIPTransportClosedError,
    EIPTransportError,
)
from pydantic import JsonValue

from .._file_patterns import PATTERN_HINTS
from ..models import (
    DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS,
    EnvironmentError,
    EnvironmentOperationReceipt,
)

_UINT64_MAX = 2**64 - 1


def session_client(session: EIPSession) -> eip.EIPClient:
    """Normalize synchronous session access before an async operation is built."""
    try:
        return session.client
    except EIPSessionStateError as error:
        raise_converted(error)


def new_context(*, timeout_seconds: float = DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS) -> eip.EIPCallContext:
    return eip.EIPCallContext(
        operation_id=f"op-{secrets.token_urlsafe(9)}",
        timeout_ms=seconds_to_milliseconds(timeout_seconds),
    )


def seconds_to_milliseconds(value: float) -> int:
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be positive and finite")
    milliseconds = math.ceil(value * 1_000)
    if milliseconds > _UINT64_MAX:
        raise ValueError("timeout exceeds the EIP uint64 range")
    return milliseconds


def encode_bytes(value: bytes) -> eip.EncodedBytes:
    return eip.EncodedBytes(
        encoding="base64",
        data=base64.b64encode(value).decode().rstrip("="),
    )


def decode_bytes(value: eip.EncodedBytes) -> bytes:
    return base64.b64decode(value.data + "=" * (-len(value.data) % 4), validate=True)


def parse_timestamp(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def convert_receipt(
    receipt: eip.OperationReceipt,
    *,
    environment_id: str,
    mount_id: str,
    generation: str,
) -> EnvironmentOperationReceipt:
    if receipt.environment_id != environment_id or str(receipt.generation) != generation:
        raise EnvironmentError(
            "EIP receipt identity does not match the bound environment",
            code="environment_stale_mount",
            retry_hint="new_run",
        )
    return EnvironmentOperationReceipt(
        mount_id=mount_id,
        observed_generation=generation,
        operation_id=receipt.operation_id,
        stage=receipt.stage.value,
        outcome=None if receipt.outcome is None else receipt.outcome.value,
    )


def convert_error(error: BaseException) -> EnvironmentError:
    if isinstance(error, EnvironmentError):
        return error
    if isinstance(error, EIPMethodError):
        error_type = error.error.data.error_type
        code = {
            eip.ErrorType.INVALID_PARAMS: "environment_request_invalid",
            eip.ErrorType.DENIED: "environment_denied",
            eip.ErrorType.NOT_FOUND_OR_DENIED: "environment_not_found",
            eip.ErrorType.UNSUPPORTED: "environment_unsupported",
            eip.ErrorType.STALE_GENERATION: "environment_stale_mount",
            eip.ErrorType.INVALID_HANDLE: "environment_not_found",
            eip.ErrorType.BUSY: "environment_busy",
            eip.ErrorType.QUOTA_EXCEEDED: "environment_too_large",
            eip.ErrorType.OUTPUT_LIMIT_EXCEEDED: "environment_too_large",
            eip.ErrorType.TIMEOUT: "environment_timeout",
            eip.ErrorType.CANCELLED: "environment_cancelled",
            eip.ErrorType.UNKNOWN_OUTCOME: "environment_unknown_outcome",
            eip.ErrorType.OPERATION_IN_PROGRESS: "environment_busy",
            eip.ErrorType.PROVIDER_UNAVAILABLE: "environment_unavailable",
            eip.ErrorType.CONFLICT: "environment_conflict",
        }.get(error_type, "environment_provider_failure")
        data = error.error.data
        details: dict[str, JsonValue] = {
            "dispatch_stage": data.dispatch_stage.value,
            "provider_retry_hint": data.retry_hint.value,
        }
        fields = {
            "query": "pattern",
            "include_pattern": "include",
            "pattern": "pattern",
            "root": "root",
            "path": "path",
            "source": "source",
            "destination": "destination",
            "cwd": "cwd",
            "line_offset": "line_offset",
            "line_limit": "line_limit",
            "max_line_length": "max_line_length",
            "offset": "offset",
            "max_results": "max_results",
            "context_lines": "context_lines",
            "max_matches_per_file": "max_matches_per_file",
            "max_files": "max_files",
            "max_file_bytes": "max_file_bytes",
        }
        field = fields.get(data.field or "")
        if field is not None:
            details["field"] = field
        hints = {
            **PATTERN_HINTS,
            "not_searchable": "Select a regular file or directory; special files cannot be searched.",
            "not_directory": "Select a directory; use text search to search a single file.",
            "not_file": "Select a regular file, not a directory or special file.",
            "invalid_value": "Use a value within the field's documented range.",
        }
        if error_type == eip.ErrorType.INVALID_PARAMS and data.safe_detail in hints:
            details["reason"] = data.safe_detail
            details["hint"] = hints[data.safe_detail]
        for key, value in (
            ("emitted_items", data.emitted_items),
            ("produced_bytes", data.produced_bytes),
            ("dropped_items", data.dropped_items),
        ):
            if value is not None:
                details[key] = value
        return EnvironmentError(
            "EIP operation failed",
            code=code,
            details=details,
            retry_hint="reconcile_first" if code == "environment_unknown_outcome" else None,
        )
    if isinstance(error, EIPSessionStateError | EIPTransportClosedError | EIPTransportError):
        return EnvironmentError(
            "EIP environment is unavailable",
            code="environment_unavailable",
            retry_hint="new_run",
        )
    if isinstance(error, EIPProtocolError):
        return EnvironmentError(
            "EIP provider violated the protocol",
            code="environment_provider_failure",
            retry_hint="new_run",
        )
    return EnvironmentError("EIP provider operation failed", code="environment_provider_failure")


def raise_converted(error: BaseException) -> Never:
    raise convert_error(error) from error


async def invoke[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except asyncio.CancelledError:
        raise
    except BaseException as error:
        raise_converted(error)
