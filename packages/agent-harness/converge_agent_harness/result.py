"""Normalized process-local Harness outcomes."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, computed_field, field_validator
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.usage import RunUsage

from converge_agent_harness.errors import RetryHint, RunError
from converge_agent_harness.state import HarnessState, decode_messages, encode_messages
from converge_agent_harness.usage import ModelUsageRecord, ProviderUsageRecord, UsageRecord

RunStatus = Literal["completed", "suspended", "failed", "cancelled"]
SuspendReason = Literal["deferred"]

_FAILURE_DETAILS_ADAPTER = TypeAdapter(dict[str, JsonValue])
_EMPTY_FAILURE_DETAILS_JSON = _FAILURE_DETAILS_ADAPTER.dump_json({})
_UNSET = object()


class SafeFailure(BaseModel):
    """Bounded serializable failure safe to expose outside the process."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details_json: bytes = Field(
        default=_EMPTY_FAILURE_DETAILS_JSON,
        alias="details",
        exclude=True,
        repr=False,
    )
    retry_hint: RetryHint = "none"

    @field_validator("details_json", mode="before")
    @classmethod
    def _encode_details(cls, value: Any) -> bytes:
        return _FAILURE_DETAILS_ADAPTER.dump_json(_FAILURE_DETAILS_ADAPTER.validate_python(value))

    @computed_field
    @property
    def details(self) -> dict[str, JsonValue]:
        """Return detached safe failure details."""
        return _FAILURE_DETAILS_ADAPTER.validate_json(self.details_json)


class HarnessRunResult[OutputT]:
    """One immutable terminal candidate or successfully delivered outcome."""

    __slots__ = (
        "_deferred",
        "_failure",
        "_messages_json",
        "_new_message_index",
        "_output",
        "_run_id",
        "_state",
        "_status",
        "_suspend_reason",
        "_thread_id",
        "_usage",
        "_usage_records",
    )

    def __init__(
        self,
        *,
        thread_id: str,
        run_id: str,
        status: RunStatus,
        output: OutputT | None,
        state: HarnessState | None,
        usage: RunUsage,
        usage_records: Sequence[UsageRecord] = (),
        failure: SafeFailure | None = None,
        suspend_reason: SuspendReason | None = None,
        deferred: DeferredToolRequests | None = None,
        _messages: tuple[ModelMessage, ...] = (),
        _new_message_index: int = 0,
    ) -> None:
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be a non-blank string")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-blank string")
        if status not in {"completed", "suspended", "failed", "cancelled"}:
            raise ValueError(f"Unsupported Harness run status: {status!r}")
        if state is not None and not isinstance(state, HarnessState):
            raise TypeError("state must be HarnessState or None")
        if state is not None and state.thread_id != thread_id:
            raise ValueError("state.thread_id must match thread_id")
        if not isinstance(usage, RunUsage):
            raise TypeError("usage must be RunUsage")
        if not all(isinstance(record, ModelUsageRecord | ProviderUsageRecord) for record in usage_records):
            raise TypeError("usage_records must contain only UsageRecord values")
        if failure is not None and not isinstance(failure, SafeFailure):
            raise TypeError("failure must be SafeFailure or None")
        if suspend_reason is not None and suspend_reason != "deferred":
            raise ValueError(f"Unsupported suspend reason: {suspend_reason!r}")
        if deferred is not None and not isinstance(deferred, DeferredToolRequests):
            raise TypeError("deferred must be DeferredToolRequests or None")

        messages_json = encode_messages(_messages)
        if not isinstance(_new_message_index, int) or not 0 <= _new_message_index <= len(_messages):
            raise ValueError("new message index is outside the message history")

        if status == "completed":
            valid = state is not None and failure is None and suspend_reason is None and deferred is None
        elif status == "suspended":
            valid = (
                state is not None
                and output is None
                and failure is None
                and suspend_reason == "deferred"
                and deferred is not None
            )
        elif status == "failed":
            valid = output is None and failure is not None and suspend_reason is None and deferred is None
        else:
            valid = output is None and failure is None and suspend_reason is None and deferred is None
        if not valid:
            raise ValueError(f"Invalid HarnessRunResult field combination for status {status!r}.")

        self._thread_id = thread_id
        self._run_id = run_id
        self._status = status
        self._output = deepcopy(output)
        self._state = state.model_copy(deep=True) if state is not None else None
        self._usage = _copy_usage(usage)
        self._usage_records = tuple(record.model_copy(deep=True) for record in usage_records)
        self._failure = failure.model_copy(deep=True) if failure is not None else None
        self._suspend_reason = suspend_reason
        self._deferred = deepcopy(deferred)
        self._messages_json = messages_json
        self._new_message_index = _new_message_index

    @property
    def thread_id(self) -> str:
        return self._thread_id

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def status(self) -> RunStatus:
        return cast(RunStatus, self._status)

    @property
    def output(self) -> OutputT | None:
        return deepcopy(self._output)

    @property
    def state(self) -> HarnessState | None:
        return self._state.model_copy(deep=True) if self._state is not None else None

    @property
    def usage(self) -> RunUsage:
        return _copy_usage(self._usage)

    @property
    def usage_records(self) -> tuple[UsageRecord, ...]:
        """Return detached mixed-source attribution records captured at the terminal boundary."""
        return tuple(record.model_copy(deep=True) for record in self._usage_records)

    @property
    def failure(self) -> SafeFailure | None:
        return self._failure.model_copy(deep=True) if self._failure is not None else None

    @property
    def suspend_reason(self) -> SuspendReason | None:
        return cast(SuspendReason | None, self._suspend_reason)

    @property
    def deferred(self) -> DeferredToolRequests | None:
        return deepcopy(self._deferred)

    def all_messages(self) -> tuple[ModelMessage, ...]:
        """Return a fresh complete message view for this run."""
        return decode_messages(self._messages_json)

    def new_messages(self) -> tuple[ModelMessage, ...]:
        """Return a fresh view of messages created by this run."""
        return self.all_messages()[self._new_message_index :]

    def replace(
        self,
        *,
        output: Any = _UNSET,
        state: Any = _UNSET,
        usage: Any = _UNSET,
        usage_records: Any = _UNSET,
        failure: Any = _UNSET,
        suspend_reason: Any = _UNSET,
        deferred: Any = _UNSET,
        status: Any = _UNSET,
    ) -> HarnessRunResult[Any]:
        """Create a detached replacement candidate while preserving Thread and Run correlation."""
        return HarnessRunResult(
            thread_id=self.thread_id,
            run_id=self.run_id,
            status=cast(RunStatus, self.status if status is _UNSET else status),
            output=self.output if output is _UNSET else output,
            state=self.state if state is _UNSET else state,
            usage=self.usage if usage is _UNSET else usage,
            usage_records=self.usage_records if usage_records is _UNSET else usage_records,
            failure=self.failure if failure is _UNSET else failure,
            suspend_reason=self.suspend_reason if suspend_reason is _UNSET else suspend_reason,
            deferred=self.deferred if deferred is _UNSET else deferred,
            _messages=self.all_messages(),
            _new_message_index=self._new_message_index,
        )

    def raise_for_status(self) -> None:
        """Raise a stable RunError unless this result completed successfully."""
        if self.status == "completed":
            return
        if self.status == "failed":
            failure = self.failure
            assert failure is not None
            raise RunError(
                failure.message,
                code=failure.code,
                details=failure.details,
                retry_hint=failure.retry_hint,
            )
        if self.status == "suspended":
            raise RunError("The run is suspended.", code="run_suspended", retry_hint="new_run")
        raise RunError("The run was cancelled.", code="run_cancelled", retry_hint="new_run")

    def output_or_raise(self) -> OutputT:
        """Return validated output, preserving a valid None-valued output."""
        self.raise_for_status()
        return cast(OutputT, self.output)

    def __repr__(self) -> str:
        return (
            f"HarnessRunResult(thread_id={self.thread_id!r}, run_id={self.run_id!r}, "
            f"status={self.status!r}, output={self.output!r})"
        )


def _copy_usage(usage: RunUsage) -> RunUsage:
    return deepcopy(usage)
