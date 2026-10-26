"""Delete unused providers, including references retained by execution history."""

from sqlalchemy import cast, delete, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH

from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.errors import conflict
from a13n_service.infra.http import require_match
from a13n_service.infra.outbox import OutboxRow
from a13n_service.resources.agents.tables import AgentRevisionRow
from a13n_service.resources.connections.tables import ConnectionRow
from a13n_service.resources.memories.tables import MemoryRow
from a13n_service.resources.models.tables import ModelRow
from a13n_service.resources.providers.tables import (
    ConnectorProviderRow,
    MemoryProviderRow,
    ModelProviderOAuthRow,
    ModelProviderRow,
    ProviderRow,
    WebProviderRow,
)
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.runs.schedules.tables import ScheduleRow
from a13n_service.runs.tables import InboxEntryRow, RunRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


async def delete_provider(
    storage: Storage,
    actor: Principal,
    row_type: type[ProviderRow],
    workspace_id: str,
    provider_id: str,
    *,
    if_match: str | None,
) -> None:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await find_row(session, actor, row_type, scope, provider_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        if row_type is WebProviderRow:
            # Overrides can nest subagents. Match provider IDs at any depth, including disabled selections.
            for owner, config in (
                (AgentRevisionRow, AgentRevisionRow.config),
                (RunRow, RunRow.options),
                (InboxEntryRow, InboxEntryRow.options),
                (ScheduleRow, ScheduleRow.invocation),
            ):
                referenced = await session.scalar(
                    select(owner.id)
                    .where(
                        owner.workspace_id == workspace_id,
                        func.jsonb_path_exists(
                            config,
                            cast("$.**.provider_id ? (@ == $id)", JSONPATH),
                            literal({"id": provider_id}, type_=JSONB),
                        ),
                    )
                    .limit(1)
                )
                if referenced:
                    raise conflict(row.KIND, row.id, "provider_in_use")
        else:
            owner, foreign_key = {
                ModelProviderRow: (ModelRow, ModelRow.provider_id),
                ConnectorProviderRow: (ConnectionRow, ConnectionRow.connector_provider_id),
                MemoryProviderRow: (MemoryRow, MemoryRow.provider_id),
            }[row_type]
            if await session.scalar(
                select(owner.id)
                .where(
                    owner.workspace_id == workspace_id,
                    foreign_key == provider_id,
                )
                .limit(1)
            ):
                raise conflict(row.KIND, row.id, "provider_in_use")
        if row_type is MemoryProviderRow and await session.scalar(
            select(OutboxRow.id)
            .where(
                OutboxRow.workspace_id == workspace_id,
                OutboxRow.kind == "memory_purge",
                OutboxRow.status == "pending",
                OutboxRow.target["provider_id"].astext == provider_id,
            )
            .limit(1)
        ):
            raise conflict(row.KIND, row.id, "provider_in_use")
        if row_type is ModelProviderRow:
            await session.execute(delete(ModelProviderOAuthRow).where(ModelProviderOAuthRow.provider_id == provider_id))
        audit_row(session, actor, row, "delete")
        await session.delete(row)
        await session.flush()
