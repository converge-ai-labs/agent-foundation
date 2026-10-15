"""Refresh existing deployment-owned presets after every serving and executing role has been upgraded."""

from collections.abc import Sequence
from dataclasses import dataclass

from a13n_logging import get_logger
from sqlalchemy import select

from a13n_service.infra.db import Storage, assign, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources import revisions
from a13n_service.resources.agents import composer, finding, presets
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.rows import audit_row
from a13n_service.tenancy.authorize import WorkspaceScope

logger = get_logger(__name__)


@dataclass
class RefreshResult:
    updated: int = 0
    unchanged: int = 0
    failed: int = 0


async def refresh(storage: Storage, *, preferred: Sequence[str]) -> RefreshResult:
    """Operator-only, with no user impersonation or credentials. Each preset commits independently.

    These definitions come exclusively from this build's factories, never caller-provided configuration.
    They reference only a workspace model. Memory Organizer is prepared with the requested Space's model
    for each organization Run and retains that separate lifecycle.
    """
    result = RefreshResult()
    cursor = ""
    while True:
        async with short_session(storage) as session:
            ids = (
                await session.scalars(
                    select(AgentRow.id)
                    .where(
                        AgentRow.source == "builtin",
                        AgentRow.preset_kind.in_(("composer", "finding")),
                        AgentRow.id > cursor,
                    )
                    .order_by(AgentRow.id)
                    .limit(100)
                )
            ).all()
        if not ids:
            break
        for agent_id in ids:
            try:
                changed = await _refresh(storage, agent_id, preferred)
            except Exception as error:
                # A failed workspace must not prevent the others from updating. Do not log raw configuration,
                # exception text or tracebacks: errors can include provider credentials or private content.
                result.failed += 1
                logger.error(
                    "Builtin Agent refresh failed",
                    extra={
                        "agent_id": agent_id,
                        "error_type": type(error).__name__,
                        "reason": (
                            "model_required"
                            if isinstance(error, ServiceError) and error.details.get("reason") == "model_required"
                            else error.code
                            if isinstance(error, ServiceError)
                            else "internal_error"
                        ),
                    },
                )
            else:
                if changed:
                    result.updated += 1
                else:
                    result.unchanged += 1
                logger.info("Builtin Agent refreshed", extra={"agent_id": agent_id, "changed": changed})
        cursor = ids[-1]
    return result


async def _refresh(storage: Storage, agent_id: str, preferred: Sequence[str]) -> bool:
    async with transaction(storage) as session:
        head = await session.scalar(
            select(AgentRow).where(AgentRow.id == agent_id, AgentRow.source == "builtin").with_for_update()
        )
        if head is None or head.preset_kind not in {"composer", "finding"}:
            return False
        definition = composer if head.preset_kind == "composer" else finding
        current = (
            None
            if head.default_revision_id is None
            else await session.get_one(AgentRevisionRow, head.default_revision_id)
        )
        model = await presets.select_model(
            session,
            WorkspaceScope(head.organization_id, head.workspace_id),
            current,
            preferred if head.preset_kind == "composer" else (),
        )
        config = definition.configuration(model)
        fields = assign(head, {"name": definition.NAME, "description": definition.DESCRIPTION})
        revision, created = await revisions.publish(session, head, AgentRevisionRow, config, actor=None, note=None)
        if not fields and not created:
            return False
        head.updated_by_id = None
        audit_row(
            session,
            None,
            head,
            f"{head.preset_kind}.refresh",
            {"authority": "operator", "revision_id": revision.id, "fields": list(fields)},
        )
        return True
