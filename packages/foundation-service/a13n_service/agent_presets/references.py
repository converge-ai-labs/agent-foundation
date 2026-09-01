"""Cross-domain retained-reference checks owned by Agent Management."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectors.models import ConnectorRevisionRecord

from .models import AgentPresetRevisionRecord


class AgentPresetConnectorReferenceChecker:
    """Protect Connectors selected by any immutable AgentPresetRevision."""

    async def has_agent_preset_reference(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        workspace_id: str,
        connector_id: str,
    ) -> bool:
        connector_revision_ids = frozenset(
            await session.scalars(
                select(ConnectorRevisionRecord.id).where(
                    ConnectorRevisionRecord.organization_id == organization_id,
                    ConnectorRevisionRecord.workspace_id == workspace_id,
                    ConnectorRevisionRecord.connector_id == connector_id,
                )
            )
        )
        if not connector_revision_ids:
            return False
        frozen_selections = await session.scalars(
            select(AgentPresetRevisionRecord.resolved_connectors).where(
                AgentPresetRevisionRecord.organization_id == organization_id,
                AgentPresetRevisionRecord.workspace_id == workspace_id,
            )
        )
        return any(
            selection.get("connector_revision_id") in connector_revision_ids
            for selections in frozen_selections
            for selection in selections
        )
