"""Portable message and Capability continuation state."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, computed_field, create_model, field_validator
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from a13n_harness._json import dump_json_bytes
from a13n_harness.errors import StateError
from a13n_harness.providers.environment.models import EnvironmentState

_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)
_ENVIRONMENT_STATES_ADAPTER = TypeAdapter(dict[str, EnvironmentState])
_EMPTY_MESSAGES_JSON = ModelMessagesTypeAdapter.dump_json([])


def _new_thread_id() -> str:
    return f"thread_{uuid4().hex}"


def encode_messages(messages: Any) -> bytes:
    """Validate and encode a detached canonical Pydantic AI message sequence."""
    validated = ModelMessagesTypeAdapter.validate_python(messages)
    return ModelMessagesTypeAdapter.dump_json(validated)


def decode_messages(value: bytes) -> tuple[ModelMessage, ...]:
    """Decode a fresh message sequence from canonical bytes."""
    return tuple(ModelMessagesTypeAdapter.validate_json(value))


def clone_messages(messages: Any) -> tuple[ModelMessage, ...]:
    """Return a message sequence with no mutable aliases to the input."""
    return decode_messages(encode_messages(messages))


class CapabilityState(BaseModel):
    """Versioned JSON state owned by one stable Capability ID."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    data_json: bytes | JsonValue = Field(alias="data", exclude=True, repr=False)

    @field_validator("data_json", mode="before")
    @classmethod
    def _encode_data(cls, value: Any) -> bytes:
        validated = _JSON_VALUE_ADAPTER.validate_python(value)
        try:
            return dump_json_bytes(validated, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("Capability state must be finite canonical JSON.") from exc

    @computed_field
    @property
    def data(self) -> JsonValue:
        """Return a detached copy of the Capability-owned JSON value."""
        return _JSON_VALUE_ADAPTER.validate_json(cast(bytes, self.data_json))


class _SelectedCapabilityState(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_by_name=False)

    entry: CapabilityState | None = None


_CAPABILITY_ENTRIES_ADAPTER = TypeAdapter(dict[str, CapabilityState])
_EMPTY_ENTRIES_JSON = _CAPABILITY_ENTRIES_ADAPTER.dump_json({})


class AgentContextStateSnapshot(BaseModel):
    """Immutable encoded copy of all Capability namespaces."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entries_json: bytes | dict[str, CapabilityState] = Field(
        default=_EMPTY_ENTRIES_JSON,
        alias="entries",
        exclude=True,
        repr=False,
    )

    @field_validator("entries_json", mode="before")
    @classmethod
    def _encode_entries(cls, value: Any) -> bytes:
        entries = _CAPABILITY_ENTRIES_ADAPTER.validate_python(value)
        for capability_id in entries:
            if not capability_id.strip():
                raise ValueError("Capability state IDs must not be blank.")
        return _CAPABILITY_ENTRIES_ADAPTER.dump_json(entries)

    def get(self, capability_id: str) -> CapabilityState | None:
        """Decode one detached namespace without materializing unrelated payloads."""
        selected = create_model(
            "SelectedCapabilityState",
            __base__=_SelectedCapabilityState,
            entry=(CapabilityState | None, Field(default=None, alias=capability_id)),
        )
        return selected.model_validate_json(cast(bytes, self.entries_json)).entry

    @computed_field
    @property
    def entries(self) -> dict[str, CapabilityState]:
        """Return a detached copy of every Capability namespace."""
        return _CAPABILITY_ENTRIES_ADAPTER.validate_json(cast(bytes, self.entries_json))


class HarnessState(BaseModel):
    """The complete portable continuation value understood by the Harness."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"]
    thread_id: str = Field(
        pattern=r"^(?:thread-[a-f0-9]{32}|[a-z][a-z0-9]*_[a-z0-9]+)$",
        max_length=256,
    )
    message_history_json: bytes | Sequence[ModelMessage] = Field(
        default=_EMPTY_MESSAGES_JSON,
        alias="message_history",
        exclude=True,
        repr=False,
    )
    agent_context_state: AgentContextStateSnapshot = Field(default_factory=AgentContextStateSnapshot)
    environment_states_json: bytes | Mapping[str, EnvironmentState] = Field(
        default=b"{}",
        alias="environment_states",
        exclude=True,
        repr=False,
    )

    @classmethod
    def new(
        cls,
        *,
        thread_id: str | None = None,
        message_history: Sequence[ModelMessage] = (),
        agent_context_state: AgentContextStateSnapshot | None = None,
        environment_states: Mapping[str, EnvironmentState] | None = None,
    ) -> HarnessState:
        """Create the initial continuation envelope for a new Thread."""
        return cls(
            schema_version="1",
            thread_id=thread_id if thread_id is not None else _new_thread_id(),
            message_history=message_history,
            agent_context_state=(
                agent_context_state if agent_context_state is not None else AgentContextStateSnapshot()
            ),
            environment_states=environment_states or {},
        )

    @field_validator("environment_states_json", mode="before")
    @classmethod
    def _encode_environment_states(cls, value: Any) -> bytes:
        validated = _ENVIRONMENT_STATES_ADAPTER.validate_python(value)
        try:
            return _ENVIRONMENT_STATES_ADAPTER.dump_json(validated)
        except (TypeError, ValueError) as exc:
            raise ValueError("Environment states must be finite canonical JSON.") from exc

    @field_validator("message_history_json", mode="before")
    @classmethod
    def _encode_message_history(cls, value: Any) -> bytes:
        try:
            return encode_messages(value)
        except Exception as exc:
            raise ValueError("Invalid Agent message history.") from exc

    @computed_field
    @property
    def message_history(self) -> tuple[ModelMessage, ...]:
        """Return a detached copy of the continuation history."""
        return decode_messages(cast(bytes, self.message_history_json))

    @computed_field
    @property
    def environment_states(self) -> dict[str, EnvironmentState]:
        """Return detached portable state for each stateful Environment mount."""
        return _ENVIRONMENT_STATES_ADAPTER.validate_json(cast(bytes, self.environment_states_json))

    def fork(self, *, thread_id: str | None = None) -> HarnessState:
        """Copy portable continuation data into a distinct generated or Host-selected Thread."""
        from a13n_harness.capabilities.subagents import _fork_inline_subagent_state

        selected_thread_id = thread_id if thread_id is not None else _new_thread_id()
        if selected_thread_id == self.thread_id:
            raise ValueError("fork thread_id must differ from the source Thread")
        return HarnessState.new(
            thread_id=selected_thread_id,
            message_history=self.message_history,
            agent_context_state=_fork_inline_subagent_state(self.agent_context_state),
            environment_states={},
        )


class AgentContextState:
    """Run-local mutable coordinator for isolated Capability namespaces."""

    def __init__(self, snapshot: AgentContextStateSnapshot | None = None) -> None:
        self._entries = snapshot.entries if snapshot is not None else {}
        self._lock = asyncio.Lock()

    async def read[T: BaseModel](
        self,
        capability_id: str,
        state_type: type[T],
        *,
        version: str,
    ) -> T | None:
        """Read and validate one Capability's state without exposing shared bytes."""
        _validate_namespace(capability_id, version)
        async with self._lock:
            entry = self._entries.get(capability_id)
            if entry is None:
                return None
            if entry.version != version:
                raise StateError(
                    "Capability state version is not supported.",
                    code="capability_state_version_unsupported",
                    details={
                        "capability_id": capability_id,
                        "expected_version": version,
                        "actual_version": entry.version,
                    },
                )
            value = entry.data
        try:
            return state_type.model_validate(value)
        except Exception as exc:
            raise StateError(
                "Capability state payload is invalid.",
                code="capability_state_invalid",
                details={"capability_id": capability_id},
            ) from exc

    async def write(
        self,
        capability_id: str,
        value: BaseModel,
        *,
        version: str,
    ) -> None:
        """Atomically replace one Capability's state namespace."""
        _validate_namespace(capability_id, version)
        entry = CapabilityState(version=version, data=value.model_dump(mode="json"))
        async with self._lock:
            self._entries[capability_id] = entry

    async def snapshot(self) -> AgentContextStateSnapshot:
        """Return a detached snapshot suitable for a HarnessState envelope."""
        async with self._lock:
            return AgentContextStateSnapshot(entries=self._entries)


def _validate_namespace(capability_id: str, version: str) -> None:
    if not capability_id.strip():
        raise StateError("Capability ID must not be blank.", code="capability_state_id_invalid")
    if not version.strip():
        raise StateError(
            "Capability state version must not be blank.",
            code="capability_state_version_invalid",
            details={"capability_id": capability_id},
        )
