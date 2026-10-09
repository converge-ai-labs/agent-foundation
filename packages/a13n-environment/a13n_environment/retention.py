"""Bounded retained-output values and exact opaque provider scalars."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, final

from pydantic import BaseModel, ConfigDict, Field, GetCoreSchemaHandler, model_validator
from pydantic_core import CoreSchema, core_schema

from .models import EnvironmentOperationReceipt


class _ExactOpaqueProviderValue:
    __slots__ = ("__payload",)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if any(base is not _ExactOpaqueProviderValue for base in cls.__bases__):
            raise TypeError("Opaque provider values cannot be subclassed")

    def __new__(cls, payload: str, *, _provider_adapter: bool = False):
        if not _provider_adapter:
            raise TypeError("Opaque provider values are constructed only by provider adapters")
        if not isinstance(payload, str) or not payload or len(payload) > 1024:
            raise ValueError("Opaque provider payload must be a non-empty bounded string")
        value = super().__new__(cls)
        object.__setattr__(value, "_ExactOpaqueProviderValue__payload", payload)
        return value

    def __init__(self, payload: str, *, _provider_adapter: bool = False) -> None:
        del payload, _provider_adapter

    @classmethod
    def _from_payload(cls, payload: str):
        return cls(payload, _provider_adapter=True)

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: object,
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        del source_type, handler

        def validate(value: object) -> object:
            if type(value) is not cls:
                raise ValueError(f"Expected exact {cls.__name__} instance")
            return value

        return core_schema.no_info_plain_validator_function(validate)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"

    __str__ = __repr__

    def __hash__(self) -> int:
        return hash((type(self), self.__payload))

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and _unwrap_opaque(other, type(self)) == self.__payload


@final
class OpaqueProcessHandle(_ExactOpaqueProviderValue):
    pass


@final
class OpaqueOutputReference(_ExactOpaqueProviderValue):
    pass


@final
class OpaqueOutputCursor(_ExactOpaqueProviderValue):
    pass


def _unwrap_opaque(value: object, expected_type: type[_ExactOpaqueProviderValue]) -> str:
    if type(value) is not expected_type:
        raise TypeError(f"Expected exact {expected_type.__name__} instance")
    return object.__getattribute__(value, "_ExactOpaqueProviderValue__payload")


class EnvironmentOutputPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_inline_bytes: int = Field(gt=0)
    max_output_bytes: int = Field(gt=0)
    overflow: Literal["fail", "truncate", "retain"]

    @model_validator(mode="after")
    def _ordered_limits(self) -> EnvironmentOutputPolicy:
        if self.max_inline_bytes > self.max_output_bytes:
            raise ValueError("max_inline_bytes cannot exceed max_output_bytes")
        return self


class BoundOutputReference(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    execution_id: str
    observed_generation: str
    reference: OpaqueOutputReference


class BoundOutputCursor(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    execution_id: str
    observed_generation: str
    cursor: OpaqueOutputCursor


class EnvironmentOutputSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_offset: int = Field(ge=0)
    data: bytes


class EnvironmentOutputCapture(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["empty", "inline", "retained", "truncated"]
    origin: Literal["native_bytes", "sdk_text"] = "native_bytes"
    coverage: Literal["complete", "partial", "unknown"] = "complete"
    observation_closed: bool = False
    reason: Literal["reattached", "connection_lost", "observation_limit", "observation_evicted"] | None = None
    producer_complete: bool | None
    content_complete: bool
    produced_bytes: int | None = Field(default=None, ge=0)
    captured_bytes: int = Field(ge=0)
    dropped_bytes: int | None = Field(default=None, ge=0)
    inline: bytes | None = None
    preview: tuple[EnvironmentOutputSegment, ...] = ()
    reference: BoundOutputReference | None = None
    cursor: BoundOutputCursor | None = None
    available_start: int = Field(default=0, ge=0)
    available_end: int = Field(default=0, ge=0)
    expires_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _native_observation_evidence(cls, value: Any) -> Any:
        if not isinstance(value, dict) or value.get("origin", "native_bytes") != "native_bytes":
            return value
        value = dict(value)
        value.setdefault("observation_closed", value.get("producer_complete") is True)
        dropped = value.get("dropped_bytes")
        if isinstance(dropped, int) and dropped > 0:
            value.setdefault("coverage", "partial")
        return value


class EnvironmentOutputReadResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunks: tuple[EnvironmentOutputSegment, ...]
    next_cursor: BoundOutputCursor | None
    capture: EnvironmentOutputCapture


class ProviderOutputOperations(Protocol):
    async def read(
        self,
        reference: BoundOutputReference,
        *,
        cursor: BoundOutputCursor | None = None,
        start_offset: int | None = None,
        policy: EnvironmentOutputPolicy,
    ) -> EnvironmentOutputReadResult: ...

    async def release(
        self,
        *,
        reference: BoundOutputReference | None = None,
        cursor: BoundOutputCursor | None = None,
    ) -> EnvironmentOperationReceipt: ...


async def materialize_capture(
    outputs: ProviderOutputOperations,
    capture: EnvironmentOutputCapture,
    policy: EnvironmentOutputPolicy,
) -> EnvironmentOutputCapture:
    """Resolve an operation's own retained output into its bounded inline result."""
    if capture.reference is None:
        return capture
    data = capture.inline or b""
    complete = False
    try:
        page = await outputs.read(
            capture.reference,
            start_offset=0,
            policy=policy.model_copy(update={"max_inline_bytes": policy.max_output_bytes, "overflow": "truncate"}),
        )
        data = b"".join(chunk.data for chunk in page.chunks)
        complete = capture.content_complete and len(data) == capture.captured_bytes
    except Exception:
        # Preserve the known execution outcome; never turn a read failure into a retryable command.
        pass
    finally:
        try:
            await outputs.release(reference=capture.reference)
        except Exception:
            # Provider scope cleanup owns retrying unreleased native resources.
            pass
    return capture.model_copy(
        update={
            "kind": "inline" if data else "empty",
            "inline": data,
            "preview": (),
            "reference": None,
            "cursor": None,
            "available_start": 0,
            "available_end": len(data),
            "captured_bytes": len(data),
            "content_complete": complete,
            "coverage": capture.coverage if complete else "partial",
            "dropped_bytes": None if capture.produced_bytes is None else max(0, capture.produced_bytes - len(data)),
            "expires_at": None,
        }
    )
