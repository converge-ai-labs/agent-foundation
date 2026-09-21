"""Request keys stored with the business records they create."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.digests import digest_request
from a13n_service.iam import AuthenticatedActor
from a13n_service.storage.relational import is_unique_conflict

from .idempotency import EvidenceScope
from .models import EntityRequestKey


def scope_key(scope: EvidenceScope, key_digest: str) -> str:
    boundary = scope.workspace_id or scope.organization_id
    if boundary is None:
        raise ValueError("A request key requires a Workspace or Organization boundary")
    return digest_request((boundary, scope.actor_type, scope.actor_id, scope.operation, scope.scope_id, key_digest))


def entity_key(
    actor: AuthenticatedActor,
    *,
    operation: str,
    scope_id: str,
    key_digest: str,
    workspace_id: str | None,
) -> str:
    return scope_key(
        EvidenceScope(
            workspace_id=workspace_id,
            organization_id=actor.boundary_organization_id,
            actor_type=actor.principal.principal_type.value,
            actor_id=actor.principal.principal_id,
            operation=operation,
            scope_id=scope_id,
        ),
        key_digest,
    )


async def find_by_key[Record: EntityRequestKey](session: AsyncSession, model: type[Record], key: str) -> Record | None:
    return await session.scalar(select(model).where(model.request_key == key))


def is_key_conflict(error: IntegrityError, table: str) -> bool:
    return is_unique_conflict(error, constraint=f"uq_{table}_request_key")
