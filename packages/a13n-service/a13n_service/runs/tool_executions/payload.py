"""Portable results use Pydantic AI's native ToolReturn content codec, never pickle."""

from a13n_harness.tool_execution import StoredToolExecution, ToolExecutionArtifact, ToolExecutionRecord
from pydantic import BaseModel, ConfigDict
from pydantic_ai import ToolReturn


class Artifact(BaseModel):
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")
    path: str
    data: bytes
    suffix: str


class Payload(BaseModel):
    """One immutable object contains the native result and all execution-owned spill bytes."""

    wrapped: bool
    result: ToolReturn
    artifacts: list[Artifact]
    retry_prompt: str | None = None

    @classmethod
    def encode(cls, execution: StoredToolExecution) -> bytes:
        wrapped = isinstance(execution.result, ToolReturn)
        return (
            cls(
                wrapped=wrapped,
                result=execution.result if wrapped else ToolReturn(return_value=execution.result),
                artifacts=[
                    Artifact(path=item.path, data=item.data, suffix=item.suffix) for item in execution.artifacts
                ],
                retry_prompt=execution.retry_prompt,
            )
            .model_dump_json()
            .encode()
        )

    def execution(self, record: ToolExecutionRecord) -> StoredToolExecution:
        return StoredToolExecution(
            record,
            self.result if self.wrapped else self.result.return_value,
            tuple(ToolExecutionArtifact(item.path, item.data, item.suffix) for item in self.artifacts),
            retry_prompt=self.retry_prompt,
        )
