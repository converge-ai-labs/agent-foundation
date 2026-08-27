"""A small single-process Host store for Harness checkpoint teaching purposes."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex, token_urlsafe
from typing import Literal

from a13n_harness import HarnessState
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class HostStoreError(RuntimeError):
    """A durable Host transition could not be applied."""


class ExecutionAttemptRecord(BaseModel):
    """Persisted ownership metadata; the opaque fence itself is never stored."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_attempt_id: str
    generation: int = Field(ge=1)
    fence_digest: str = Field(min_length=64, max_length=64)
    starting_checkpoint_ref: str | None = None

    @field_validator("execution_attempt_id", "starting_checkpoint_ref")
    @classmethod
    def _validate_identifiers(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        if info.field_name is None:
            raise ValueError("Identifier field name is unavailable")
        return _validated_id(value, info.field_name)


class HostExecutionRecord(BaseModel):
    """The authoritative local projection for one logical execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    execution_id: str
    definition_revision_ref: str
    agent_instance_id: str
    thread_id: str | None = None
    state: Literal["accepted", "running", "completed", "failed"] = "accepted"
    version: int = Field(default=0, ge=0)
    next_execution_attempt_generation: int = Field(default=1, ge=1)
    next_checkpoint_sequence: int = Field(default=1, ge=1)
    current_execution_attempt: ExecutionAttemptRecord | None = None
    selected_checkpoint_ref: str | None = None
    terminal_output: str | None = None

    @field_validator(
        "execution_id",
        "definition_revision_ref",
        "agent_instance_id",
        "thread_id",
        "selected_checkpoint_ref",
    )
    @classmethod
    def _validate_identifiers(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        if info.field_name is None:
            raise ValueError("Identifier field name is unavailable")
        return _validated_id(value, info.field_name)


class HostCheckpointRecord(BaseModel):
    """One immutable Host-selected continuation candidate with provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    checkpoint_ref: str
    execution_id: str
    execution_attempt_id: str
    execution_attempt_generation: int = Field(ge=1)
    sequence: int = Field(ge=1)
    thread_id: str
    harness_run_id: str = Field(min_length=1)
    harness_state: HarnessState
    effective_environment_topology_ref: str | None = None
    launch_state_ref: str | None = None

    @field_validator(
        "checkpoint_ref",
        "execution_id",
        "execution_attempt_id",
        "thread_id",
        "effective_environment_topology_ref",
        "launch_state_ref",
    )
    @classmethod
    def _validate_identifiers(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        if info.field_name is None:
            raise ValueError("Identifier field name is unavailable")
        return _validated_id(value, info.field_name)

    @model_validator(mode="after")
    def _validate_thread_correlation(self) -> HostCheckpointRecord:
        if self.thread_id != self.harness_state.thread_id:
            raise ValueError("Checkpoint thread_id must match HarnessState.thread_id")
        return self


@dataclass(frozen=True, slots=True)
class ExecutionAttemptLease:
    """Process-local proof and atomically selected starting checkpoint."""

    execution_id: str
    execution_attempt_id: str
    generation: int
    fence: str
    starting_checkpoint: HostCheckpointRecord | None


class JsonFileHostStore:
    """POSIX crash-durable files for one event-loop-owned Host instance.

    Non-POSIX platforms retain atomic process-level replacement but do not gain
    a power-loss durability claim from this teaching implementation. This is
    deliberately an embedded Host example, not a distributed lease
    implementation. A production Host replaces this class with transactional
    lifecycle authority while preserving the same Harness boundary.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = asyncio.Lock()

    async def create_execution(
        self,
        *,
        execution_id: str,
        definition_revision_ref: str,
        agent_instance_id: str,
    ) -> HostExecutionRecord:
        record = HostExecutionRecord(
            execution_id=execution_id,
            definition_revision_ref=definition_revision_ref,
            agent_instance_id=agent_instance_id,
        )
        async with self._lock:
            path = self._execution_path(execution_id)
            if await asyncio.to_thread(path.exists):
                raise HostStoreError(f"Execution {execution_id!r} already exists")
            await self._write_model(path, record)
        return record

    async def read_execution(self, execution_id: str) -> HostExecutionRecord:
        async with self._lock:
            return await self._read_execution(execution_id)

    async def acquire_execution_attempt(self, execution_id: str) -> ExecutionAttemptLease:
        async with self._lock:
            execution = await self._read_execution(execution_id)
            if execution.state in {"completed", "failed"}:
                raise HostStoreError(f"Execution {execution_id!r} is terminal")
            if execution.current_execution_attempt is not None:
                raise HostStoreError(f"Execution {execution_id!r} already has a current ExecutionAttempt")
            return await self._issue_execution_attempt(execution)

    async def replace_current_execution_attempt(self, execution_id: str) -> ExecutionAttemptLease:
        """Atomically fence a lost owner and issue a replacement under Host authority."""

        async with self._lock:
            execution = await self._read_execution(execution_id)
            if execution.state in {"completed", "failed"}:
                raise HostStoreError(f"Execution {execution_id!r} is terminal")
            if execution.current_execution_attempt is None:
                raise HostStoreError(f"Execution {execution_id!r} has no current ExecutionAttempt to replace")
            return await self._issue_execution_attempt(execution)

    async def commit_checkpoint(
        self,
        lease: ExecutionAttemptLease,
        *,
        harness_run_id: str,
        harness_state: HarnessState,
        effective_environment_topology_ref: str | None = None,
        launch_state_ref: str | None = None,
    ) -> HostCheckpointRecord:
        async with self._lock:
            record = await self._read_execution(lease.execution_id)
            self._require_current_execution_attempt(record, lease)

            selected_checkpoint = (
                await self._read_checkpoint(lease.execution_id, record.selected_checkpoint_ref)
                if record.selected_checkpoint_ref is not None
                else None
            )
            execution_thread_id = self._require_execution_thread(record, selected_checkpoint)
            if execution_thread_id is not None and harness_state.thread_id != execution_thread_id:
                raise HostStoreError("Checkpoint Thread does not match the Execution Thread")

            sequence = record.next_checkpoint_sequence
            while True:
                checkpoint_ref = f"checkpoint-{sequence}"
                checkpoint = HostCheckpointRecord(
                    checkpoint_ref=checkpoint_ref,
                    execution_id=lease.execution_id,
                    execution_attempt_id=lease.execution_attempt_id,
                    execution_attempt_generation=lease.generation,
                    sequence=sequence,
                    thread_id=harness_state.thread_id,
                    harness_run_id=harness_run_id,
                    harness_state=harness_state,
                    effective_environment_topology_ref=effective_environment_topology_ref,
                    launch_state_ref=launch_state_ref,
                )
                checkpoint_path = self._checkpoint_path(lease.execution_id, checkpoint_ref)
                if not await asyncio.to_thread(checkpoint_path.exists):
                    # Payload first, authority pointer second. A crash between
                    # these writes leaves an immutable unselected candidate.
                    await self._write_model(checkpoint_path, checkpoint)
                    break

                orphaned_checkpoint = await self._read_checkpoint(lease.execution_id, checkpoint_ref)
                if orphaned_checkpoint == checkpoint:
                    # An identical payload survived a failed authority-pointer
                    # write. Reuse it instead of wedging this sequence forever.
                    break
                sequence += 1

            selected = record.model_copy(
                update={
                    "version": record.version + 1,
                    "next_checkpoint_sequence": sequence + 1,
                    "selected_checkpoint_ref": checkpoint_ref,
                    "thread_id": harness_state.thread_id,
                }
            )
            await self._write_model(self._execution_path(lease.execution_id), selected)
            return checkpoint

    async def load_selected_checkpoint(self, execution_id: str) -> HostCheckpointRecord | None:
        async with self._lock:
            record = await self._read_execution(execution_id)
            checkpoint_ref = record.selected_checkpoint_ref
            if checkpoint_ref is None:
                return None
            checkpoint = await self._read_checkpoint(execution_id, checkpoint_ref)
            self._require_execution_thread(record, checkpoint)
            return checkpoint

    async def commit_completed(self, lease: ExecutionAttemptLease, *, output: str) -> HostExecutionRecord:
        async with self._lock:
            record = await self._read_execution(lease.execution_id)
            self._require_current_execution_attempt(record, lease)
            checkpoint_ref = record.selected_checkpoint_ref
            if checkpoint_ref is None:
                raise HostStoreError("Terminal completion requires a selected checkpoint")
            checkpoint = await self._read_checkpoint(lease.execution_id, checkpoint_ref)
            self._require_execution_thread(record, checkpoint)
            if (
                checkpoint.execution_attempt_id != lease.execution_attempt_id
                or checkpoint.execution_attempt_generation != lease.generation
            ):
                raise HostStoreError("Terminal completion requires a checkpoint from the current ExecutionAttempt")

            completed = record.model_copy(
                update={
                    "state": "completed",
                    "version": record.version + 1,
                    "current_execution_attempt": None,
                    "terminal_output": output,
                }
            )
            await self._write_model(self._execution_path(lease.execution_id), completed)
            return completed

    async def _issue_execution_attempt(self, execution: HostExecutionRecord) -> ExecutionAttemptLease:
        generation = execution.next_execution_attempt_generation
        execution_attempt_id = f"execution-attempt-{generation}"
        fence = token_urlsafe(32)
        starting_checkpoint = (
            await self._read_checkpoint(execution.execution_id, execution.selected_checkpoint_ref)
            if execution.selected_checkpoint_ref is not None
            else None
        )
        self._require_execution_thread(execution, starting_checkpoint)
        execution_attempt = ExecutionAttemptRecord(
            execution_attempt_id=execution_attempt_id,
            generation=generation,
            fence_digest=_fence_digest(fence),
            starting_checkpoint_ref=(starting_checkpoint.checkpoint_ref if starting_checkpoint is not None else None),
        )
        updated_execution = execution.model_copy(
            update={
                "state": "running",
                "version": execution.version + 1,
                "next_execution_attempt_generation": generation + 1,
                "current_execution_attempt": execution_attempt,
            }
        )
        await self._write_model(self._execution_path(execution.execution_id), updated_execution)
        return ExecutionAttemptLease(
            execution_id=execution.execution_id,
            execution_attempt_id=execution_attempt_id,
            generation=generation,
            fence=fence,
            starting_checkpoint=starting_checkpoint,
        )

    async def _read_execution(self, execution_id: str) -> HostExecutionRecord:
        path = self._execution_path(execution_id)
        try:
            payload = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except FileNotFoundError:
            raise HostStoreError(f"Execution {execution_id!r} does not exist") from None
        return HostExecutionRecord.model_validate_json(payload)

    async def _read_checkpoint(self, execution_id: str, checkpoint_ref: str) -> HostCheckpointRecord:
        path = self._checkpoint_path(execution_id, checkpoint_ref)
        try:
            payload = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except FileNotFoundError:
            raise HostStoreError(f"Selected checkpoint {checkpoint_ref!r} is missing") from None
        checkpoint = HostCheckpointRecord.model_validate_json(payload)
        if checkpoint.execution_id != execution_id:
            raise HostStoreError("Selected checkpoint belongs to another Execution")
        return checkpoint

    @staticmethod
    def _require_execution_thread(
        record: HostExecutionRecord,
        checkpoint: HostCheckpointRecord | None,
    ) -> str | None:
        if checkpoint is None:
            return record.thread_id
        if record.thread_id is not None and record.thread_id != checkpoint.thread_id:
            raise HostStoreError("Selected checkpoint Thread does not match the Execution Thread")
        return checkpoint.thread_id

    def _require_current_execution_attempt(self, record: HostExecutionRecord, lease: ExecutionAttemptLease) -> None:
        execution_attempt = record.current_execution_attempt
        if (
            execution_attempt is None
            or lease.execution_id != record.execution_id
            or lease.execution_attempt_id != execution_attempt.execution_attempt_id
            or lease.generation != execution_attempt.generation
            or not hmac.compare_digest(_fence_digest(lease.fence), execution_attempt.fence_digest)
        ):
            raise HostStoreError("ExecutionAttempt lease is stale or invalid")

    async def _write_model(self, path: Path, value: BaseModel) -> None:
        payload = value.model_dump_json(indent=2)
        # Cancelling to_thread() does not stop its worker. Join it before the
        # caller can release the transition lock and start another write.
        write_task = asyncio.create_task(asyncio.to_thread(_atomic_write_text, path, payload))
        pending_cancellation: asyncio.CancelledError | None = None
        while not write_task.done():
            try:
                await asyncio.shield(write_task)
            except asyncio.CancelledError as exc:
                pending_cancellation = pending_cancellation or exc

        write_error: BaseException | None = None
        try:
            write_task.result()
        except BaseException as exc:
            write_error = exc
        if pending_cancellation is not None:
            if write_error is not None:
                pending_cancellation.add_note(f"Host state write also failed: {write_error!r}")
            raise pending_cancellation
        if write_error is not None:
            raise write_error

    def _execution_path(self, execution_id: str) -> Path:
        validated = _validated_id(execution_id, "execution_id")
        return self._root / "executions" / validated / "execution.json"

    def _checkpoint_path(self, execution_id: str, checkpoint_ref: str) -> Path:
        validated_execution = _validated_id(execution_id, "execution_id")
        validated_checkpoint = _validated_id(checkpoint_ref, "checkpoint_ref")
        return self._root / "executions" / validated_execution / "checkpoints" / f"{validated_checkpoint}.json"


def _validated_id(value: str, field_name: str) -> str:
    if _ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a short kind-prefixed identifier")
    return value


def _fence_digest(fence: str) -> str:
    return hashlib.sha256(fence.encode("utf-8")).hexdigest()


def _atomic_write_text(path: Path, payload: str) -> None:
    _ensure_durable_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _ensure_durable_directory(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    path.mkdir(parents=True, exist_ok=True)
    for directory in reversed(missing):
        _fsync_directory(directory.parent)
        _fsync_directory(directory)


def _fsync_directory(path: Path) -> None:
    # Directory fsync is the portable POSIX durability boundary for rename and
    # mkdir metadata. Other platforms still receive atomic process-level replace.
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
