"""Portable execution results using Pydantic AI's native ToolReturn content codec."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter, field_validator
from pydantic_ai import ToolReturn

from a13n_harness._json import dump_json_bytes
from a13n_harness.tool_execution import StoredToolExecution, ToolExecutionArtifact, ToolExecutionRecord

_TOOL_RETURN_ADAPTER = TypeAdapter(ToolReturn)


class Artifact(BaseModel):
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")
    path: str
    data: bytes
    suffix: str


class ToolExecutionPayload(BaseModel):
    """Native result and execution-owned spill bytes; no executable continuation."""

    wrapped: bool
    result: ToolReturn
    artifacts: list[Artifact]
    retry_prompt: str | None = None

    @field_validator("result", mode="before")
    @classmethod
    def _decode_native_result(cls, value: Any) -> ToolReturn:
        if isinstance(value, ToolReturn):
            return value
        # Native ToolReturn owns content/file decoding. Its provenance validator
        # re-enters Python validation, so inheriting an outer strict=True would
        # reject JSON base64 strings before BinaryContent can decode them.
        return _TOOL_RETURN_ADAPTER.validate_json(dump_json_bytes(value))

    @classmethod
    def from_execution(cls, execution: StoredToolExecution) -> ToolExecutionPayload:
        wrapped = isinstance(execution.result, ToolReturn)
        return cls(
            wrapped=wrapped,
            result=execution.result if wrapped else ToolReturn(return_value=execution.result),
            artifacts=[Artifact(path=item.path, data=item.data, suffix=item.suffix) for item in execution.artifacts],
            retry_prompt=execution.retry_prompt,
        )

    @classmethod
    def encode(cls, execution: StoredToolExecution) -> bytes:
        return cls.from_execution(execution).model_dump_json().encode()

    def execution(self, record: ToolExecutionRecord) -> StoredToolExecution:
        return StoredToolExecution(
            record,
            self.result if self.wrapped else self.result.return_value,
            tuple(ToolExecutionArtifact(item.path, item.data, item.suffix) for item in self.artifacts),
            retry_prompt=self.retry_prompt,
        )
