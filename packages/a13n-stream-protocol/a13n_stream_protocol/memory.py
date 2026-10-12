"""Payload-free evidence of automatic memory delivery, not model citation attribution."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError


class RecordSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    sha256: str


class FileSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    sha256: str
    bytes: int = Field(ge=0)
    truncated: bool = False


class MemorySource(BaseModel):
    """One automatic delivery in this Run; content remains in the memory or execution archive."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["record", "file"]
    memory: str
    # Set by a managed Host from its frozen mounts, never by the model/provider.
    memory_id: str | None = None
    status: Literal["recalled", "failed", "timeout", "full", "changes", "unchanged", "omitted", "unavailable"]
    records: tuple[RecordSource, ...] = ()
    files: tuple[FileSource, ...] = ()
    # A change-feed position, not an atomic version of the loaded files.
    cursor: str | None = None
    sources_truncated: bool = False


def memory_sources(payload: dict[str, JsonValue]) -> tuple[MemorySource, ...] | None:
    """Old observations lack source evidence; never turn missing evidence into an empty result."""
    kind = {"memory_recall": "record", "memory_context": "file"}.get(str(payload.get("type")))
    memories = payload.get("memories")
    if kind is None or not isinstance(memories, list):
        return None
    sources: list[MemorySource] = []
    for memory in memories:
        if not isinstance(memory, dict):
            return None
        status = memory.get("recall" if kind == "record" else "context")
        if not isinstance(status, str):
            return None
        if (
            status in {"recalled", "full", "changes", "unchanged", "omitted"}
            and ("records" if kind == "record" else "files") not in memory
        ):
            return None
        try:
            sources.append(
                MemorySource.model_validate(
                    {
                        "kind": kind,
                        "memory": memory.get("memory"),
                        "memory_id": memory.get("memory_id"),
                        "status": status,
                        "records": memory.get("records", []),
                        "files": memory.get("files", []),
                        "cursor": memory.get("cursor"),
                        "sources_truncated": memory.get("sources_truncated", False),
                    }
                )
            )
        except ValidationError:
            # Context extensions are open: preserve unknown producer payloads as ordinary observations.
            return None
    return tuple(sources)
