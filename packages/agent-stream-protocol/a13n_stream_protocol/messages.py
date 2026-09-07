"""Shared presentation metadata for live input and retained native messages."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ContentMetadata(BaseModel):
    """Application-only presentation hints, never model instructions or authorization."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    display: bool = True
    source_id: str | None = None
    media: bool = False

    @classmethod
    def from_native(cls, metadata: object) -> ContentMetadata:
        """Project only known hints; unrelated native application metadata stays private."""
        if not isinstance(metadata, dict):
            return cls()
        source_id = metadata.get("source_id")
        return cls(
            display=metadata.get("display") is not False,
            source_id=source_id if isinstance(source_id, str) else None,
            media=metadata.get("media") is True,
        )
