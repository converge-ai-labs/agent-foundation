"""Detached Session membership facts observed by one bounded operation."""

from __future__ import annotations

from dataclasses import dataclass

from .models import SessionRecord


@dataclass(frozen=True, slots=True)
class SessionScope:
    id: str
    organization_id: str
    workspace_id: str
    configuration_owner_user_id: str | None
    configuration_draft_id: str | None

    @classmethod
    def from_record(cls, record: SessionRecord) -> SessionScope:
        return cls(
            id=record.id,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            configuration_owner_user_id=record.configuration_owner_user_id,
            configuration_draft_id=record.configuration_draft_id,
        )
