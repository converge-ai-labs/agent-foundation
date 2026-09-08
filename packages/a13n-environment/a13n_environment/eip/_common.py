from __future__ import annotations

import asyncio
import base64
import math
import secrets
from collections.abc import Awaitable
from datetime import datetime
from typing import Never

from a13n_envd_client.eip import v1 as eip
from a13n_envd_client.errors import (
    EIPMethodError,
    EIPProtocolError,
    EIPSessionStateError,
    EIPTransportClosedError,
    EIPTransportError,
)

from .._file_patterns import PATTERN_HINTS
from ..models import (
    DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS,
    EnvironmentError,
    EnvironmentOperationReceipt,
)

_UINT64_MAX = 2**64 - 1


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
            eip.ErrorType.CONFLICT: "environment_conflict",
        }.get(error_type, "environment_provider_failure")
        data = error.error.data
        details: dict[str, str] = {}
        if error_type == eip.ErrorType.INVALID_PARAMS and data.safe_detail in PATTERN_HINTS:
            field = {"query": "pattern", "include_pattern": "include", "pattern": "pattern"}.get(data.field or "")
            if field is not None and data.safe_detail is not None:
                details = {"field": field, "reason": data.safe_detail, "hint": PATTERN_HINTS[data.safe_detail]}
        return EnvironmentError("EIP operation failed", code=code, details=details)
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
