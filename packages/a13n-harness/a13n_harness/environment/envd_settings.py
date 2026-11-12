"""Platform-independent, template-editable Envd startup settings.

Only explicitly supplied values are serialized. Provider bootstrap retains
identity, transport, credentials and execution policy ownership.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    ValidationInfo,
    model_serializer,
    model_validator,
)

# Resource accounting in the supported 64-bit daemon uses usize::MAX >> 3.
MAX_RESOURCE = (2**64 - 1) >> 3


def budget(default: int, group: str, unit: str, *, minimum: int = 1) -> Any:
    return Field(
        default=default,
        strict=True,
        ge=minimum,
        le=MAX_RESOURCE,
        json_schema_extra={"x-envd-group": group, "x-envd-unit": unit},
    )


class _Overrides(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    def overrides(self) -> dict[str, Any]:
        values = {}
        for name in self.model_fields_set:
            value = getattr(self, name)
            if isinstance(value, _Overrides):
                value = value.overrides()
                if not value:
                    continue
            values[name] = value
        # Stable ordering also makes configuration fingerprints deterministic.
        return {name: values[name] for name in type(self).model_fields if name in values}

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler):
        # Keep the typed output schema while persisting only explicit overrides.
        explicit = self.overrides()
        return {name: value for name, value in handler(self).items() if name in explicit}


# Each Session budget is bounded by its corresponding Device total.
RESERVATIONS = (
    ("max_spool_bytes", "max_device_spool_bytes"),
    ("max_spool_objects", "max_device_spool_objects"),
    ("max_staged_file_bytes", "max_device_staged_file_bytes"),
    ("max_staged_file_objects", "max_device_staged_file_objects"),
    ("max_concurrent_operations", "max_device_concurrent_operations"),
    ("max_processes", "max_device_processes"),
    ("max_operation_records", "max_device_operation_records"),
    ("max_process_records", "max_device_process_records"),
    ("max_concurrent_file_transfers", "max_device_file_transfers"),
    ("max_file_transfer_records", "max_device_file_transfer_records"),
)


class EnvdLimits(_Overrides):
    """Daemon budgets, with the same defaults and constraints as config.rs."""

    max_device_spool_bytes: int = budget(4 * 1024 * 1024 * 1024, "capacity", "bytes")
    max_device_spool_objects: int = budget(65_536, "capacity", "count")
    max_device_staged_file_bytes: int = budget(32 * 1024 * 1024 * 1024, "capacity", "bytes")
    max_device_staged_file_objects: int = budget(2048, "capacity", "count")
    max_device_concurrent_operations: int = budget(256, "capacity", "count")
    max_device_processes: int = budget(512, "capacity", "count")
    max_device_operation_records: int = budget(65_536, "capacity", "count")
    max_device_process_records: int = budget(4096, "capacity", "count")
    max_device_file_transfers: int = budget(128, "capacity", "count")
    max_device_file_transfer_records: int = budget(2048, "capacity", "count")
    max_sessions: int = budget(128, "capacity", "count")
    max_file_bytes: int = budget(8 * 1024 * 1024 * 1024, "sizes", "bytes")
    max_request_bytes: int = budget(16 * 1024 * 1024, "protocol", "bytes")
    max_response_bytes: int = budget(16 * 1024 * 1024, "protocol", "bytes")
    max_concurrent_operations: int = budget(128, "capacity", "count")
    max_processes: int = budget(128, "capacity", "count")
    max_operation_duration_ms: int = budget(24 * 60 * 60 * 1000, "deadlines", "milliseconds")
    max_output_preview_bytes: int = budget(2 * 1024 * 1024, "sizes", "bytes")
    max_output_bytes_per_stream: int = budget(256 * 1024 * 1024, "sizes", "bytes")
    max_spool_bytes: int = budget(1024 * 1024 * 1024, "capacity", "bytes")
    max_spool_objects: int = budget(16_384, "capacity", "count")
    max_operation_records: int = budget(16_384, "capacity", "count")
    operation_record_ttl_ms: int = budget(24 * 60 * 60 * 1000, "retention", "milliseconds")
    max_process_records: int = budget(1024, "capacity", "count")
    max_transfer_frame_bytes: int = budget(4 * 1024 * 1024, "protocol", "bytes", minimum=26)
    max_concurrent_file_transfers: int = budget(64, "capacity", "count")
    max_file_transfer_records: int = budget(512, "capacity", "count")
    file_transfer_record_ttl_ms: int = budget(24 * 60 * 60 * 1000, "retention", "milliseconds")
    max_staged_file_bytes: int = budget(8 * 1024 * 1024 * 1024, "capacity", "bytes")
    max_staged_file_objects: int = budget(512, "capacity", "count")
    file_transfer_idle_ttl_ms: int = budget(10 * 60 * 1000, "retention", "milliseconds")
    max_file_transfer_duration_ms: int = budget(2 * 60 * 60 * 1000, "retention", "milliseconds")

    @model_validator(mode="after")
    def consistent(self, info: ValidationInfo) -> Self:
        effective = {name: getattr(self, name) for name in type(self).model_fields}
        if info.context and "envd_limit_defaults" in info.context:
            effective.update(info.context["envd_limit_defaults"])
            effective.update(self.overrides())
        for session, device in RESERVATIONS:
            if effective[session] > effective[device]:
                raise ValueError(f"{session} must not exceed {device}")
        if effective["max_process_records"] < effective["max_processes"]:
            raise ValueError("max_process_records must cover max_processes")
        if effective["max_file_transfer_records"] < effective["max_concurrent_file_transfers"]:
            raise ValueError("max_file_transfer_records must cover max_concurrent_file_transfers")
        if effective["max_output_preview_bytes"] > effective["max_output_bytes_per_stream"]:
            raise ValueError("max_output_preview_bytes must not exceed max_output_bytes_per_stream")
        if effective["max_spool_bytes"] < 2 * effective["max_output_bytes_per_stream"]:
            raise ValueError("max_spool_bytes must hold both output streams")
        return self


class EnvdSettings(_Overrides):
    limits: EnvdLimits = Field(default_factory=EnvdLimits)
    vm_startup_timeout_ms: int = budget(120_000, "deadlines", "milliseconds")
    computer_use_permission_timeout_ms: int = budget(120_000, "deadlines", "milliseconds")
    idle_timeout_ms: int = budget(3_600_000, "lifecycle", "milliseconds")
    max_lifetime_ms: int | None = Field(
        default=None,
        strict=True,
        gt=0,
        le=MAX_RESOURCE,
        json_schema_extra={"x-envd-group": "lifecycle", "x-envd-unit": "milliseconds"},
    )
