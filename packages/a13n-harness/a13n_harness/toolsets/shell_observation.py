"""Portable Shell observations and the shared non-consuming output projection."""

from __future__ import annotations

import codecs
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue

from a13n_harness.environment.commands import ProcessStatus, ProcessStreamRead
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.retention import EnvironmentOutputCapture, EnvironmentOutputSegment

from .output import tool_output_size
from .shell_control import ShellControl, ShellTarget
from .shell_results import OutputPageProjection, ProcessObservationSuccess, ProcessStatusProjection

_MAX_PROJECTED_OUTPUT_CHARS = 10_500


class ShellStream(BaseModel):
    """Original bytes and capture evidence, without provider-owned handles or cursors."""

    model_config = ConfigDict(frozen=True, ser_json_bytes="base64", val_json_bytes="base64")

    start_offset: int
    data: bytes
    capture: EnvironmentOutputCapture

    @classmethod
    def from_read(cls, stream: ProcessStreamRead, *, requested_offset: int = 0) -> ShellStream:
        return cls(
            start_offset=(
                stream.chunks[0].start_offset
                if stream.chunks
                else max(requested_offset, stream.capture.available_start)
            ),
            data=b"".join(chunk.data for chunk in stream.chunks),
            capture=stream.capture.model_copy(
                update={"inline": None, "preview": (), "reference": None, "cursor": None}
            ),
        )

    def read(self, offset: int) -> ProcessStreamRead | None:
        start = max(offset, self.capture.available_start)
        end = self.start_offset + len(self.data)
        if start < self.start_offset or start > end or (start == end and end < self.capture.available_end):
            return None
        data = self.data[start - self.start_offset :]
        return ProcessStreamRead(
            chunks=(EnvironmentOutputSegment(start_offset=start, data=data),) if data else (),
            next_cursor=None,
            capture=self.capture,
        )


class ShellObservation(BaseModel):
    """A bounded observation, never execution completion or control authority."""

    model_config = ConfigDict(frozen=True)

    target: ShellTarget
    status: ProcessStatus
    stdin_open: bool | None
    stdout: ShellStream
    stderr: ShellStream

    def read(self, execution_id: str, control: ShellControl) -> dict[str, JsonValue] | None:
        if control.operation == "describe":
            return self.target.model_dump(mode="json")
        if control.operation == "inspect":
            return {
                "ok": True,
                "process_id": execution_id,
                "status": cast(JsonValue, _project_status(self.status)),
                "stdin_open": self.stdin_open,
            }
        if control.operation != "wait" or control.timeout_seconds != 0:
            return None
        stdout, stderr = self.stdout.read(control.stdout_offset), self.stderr.read(control.stderr_offset)
        if stdout is None or stderr is None:
            return None
        return cast(
            dict[str, JsonValue],
            _process_observation(
                execution_id,
                self.status,
                self.stdin_open,
                stdout,
                stderr,
                stdout_offset=control.stdout_offset,
                stderr_offset=control.stderr_offset,
            ),
        )


def _project_status(status: ProcessStatus) -> ProcessStatusProjection:
    return {
        "phase": status.phase,
        "termination_reason": status.termination_reason,
        "exit_code": status.exit_code,
        "signal": status.signal,
        "cleanup": status.cleanup,
    }


def _project_stream(
    stream: ProcessStreamRead,
    *,
    requested_offset: int,
    data: bytes,
    full_page_bytes: int,
) -> OutputPageProjection:
    capture = stream.capture
    start_offset = stream.chunks[0].start_offset if stream.chunks else max(requested_offset, capture.available_start)
    return {
        "requested_offset": requested_offset,
        "start_offset": start_offset,
        "next_offset": start_offset + len(data),
        "available_start": capture.available_start,
        "available_end": capture.available_end,
        "origin": capture.origin,
        "coverage": capture.coverage,
        "observation_closed": capture.observation_closed,
        "reason": capture.reason,
        "produced_bytes": capture.produced_bytes,
        "producer_complete": capture.producer_complete,
        "content_complete": capture.content_complete and len(data) == full_page_bytes,
        "omitted_before_bytes": max(0, start_offset - requested_offset),
        "text": data.decode("utf-8", errors="replace"),
    }


def _process_observation(
    process_id: str,
    status: ProcessStatus,
    stdin_open: bool | None,
    stdout: ProcessStreamRead,
    stderr: ProcessStreamRead,
    *,
    stdout_offset: int,
    stderr_offset: int,
    bound_output: bool = True,
) -> ProcessObservationSuccess:
    stdout_full = b"".join(chunk.data for chunk in stdout.chunks)
    stderr_full = b"".join(chunk.data for chunk in stderr.chunks)
    budget = len(stdout_full) + len(stderr_full)
    while True:
        stdout_data, stderr_data = _fit_stream_prefixes(stdout_full, stderr_full, budget)
        result: ProcessObservationSuccess = {
            "ok": True,
            "process_id": process_id,
            "status": _project_status(status),
            "stdin_open": stdin_open,
            "stdout": _project_stream(
                stdout,
                requested_offset=stdout_offset,
                data=stdout_data,
                full_page_bytes=len(stdout_full),
            ),
            "stderr": _project_stream(
                stderr,
                requested_offset=stderr_offset,
                data=stderr_data,
                full_page_bytes=len(stderr_full),
            ),
        }
        if not bound_output or tool_output_size(cast(dict[str, JsonValue], result)) <= _MAX_PROJECTED_OUTPUT_CHARS:
            return result
        if budget == 0:
            raise EnvironmentError(
                "The process result metadata exceeds Harness output bounds.",
                code="environment_provider_failure",
            )
        budget = max(0, int(budget * 0.75) - 1)


def _fit_stream_prefixes(stdout: bytes, stderr: bytes, max_bytes: int) -> tuple[bytes, bytes]:
    """Fit fair UTF-8-safe stream prefixes inside one aggregate byte budget."""
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    stdout_limit = min(len(stdout), (max_bytes + 1) // 2)
    stderr_limit = min(len(stderr), max_bytes // 2)
    remaining = max_bytes - stdout_limit - stderr_limit
    if remaining:
        added = min(len(stdout) - stdout_limit, remaining)
        stdout_limit += added
        remaining -= added
    if remaining:
        stderr_limit += min(len(stderr) - stderr_limit, remaining)
    return _utf8_safe_prefix(stdout, stdout_limit), _utf8_safe_prefix(stderr, stderr_limit)


def _utf8_safe_prefix(data: bytes, limit: int) -> bytes:
    prefix = data[:limit]
    if limit >= len(data):
        return prefix
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    decoder.decode(prefix, final=False)
    buffered, _ = decoder.getstate()
    return prefix[: len(prefix) - len(buffered)] if buffered else prefix
