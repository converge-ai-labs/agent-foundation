"""Audit document lifecycle and access changes without storing memory content."""

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import AuthenticatedActor, PrincipalRef, PrincipalType
from a13n_service.iam.audit import security_audit_record
from a13n_service.ids import new_object_id
from a13n_service.interactions.models import RunRecord
from a13n_service.temporal import utc_now

from .access import Authority, RuntimeAuthority


async def audit(
    session: AsyncSession,
    authority: Authority,
    *,
    organization_id: str,
    workspace_id: str,
    action: str,
    resource_id: str,
    details: dict[str, object] | None = None,
) -> None:
    actor = authority
    if isinstance(authority, RuntimeAuthority):
        run = await session.get(RunRecord, authority.context().run_id)
        assert run is not None
        actor = AuthenticatedActor(
            principal=PrincipalRef(
                principal_type=PrincipalType(run.authority_principal_type), principal_id=run.authority_principal_id
            ),
            auth_method="internal",
            credential_id="bot-memory",
            boundary_workspace_id=workspace_id,
        )
        details = {**(details or {}), "run_id": run.id, "agent_id": authority.agent_id}
    assert isinstance(actor, AuthenticatedActor)
    session.add(
        security_audit_record(
            audit_id=new_object_id("aud"),
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            action=f"bot_memory.{action}",
            resource_type="bot_memory",
            resource_id=resource_id,
            outcome="success",
            occurred_at=utc_now(),
            details=details,
        )
    )
