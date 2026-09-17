"""One-way group visibility shared by navigation, reads, and search planning."""

from sqlalchemy import and_, exists, or_, select, true, tuple_
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from a13n_service.connectivity.accounts.target_models import AccountTargetRecord

from .domain import ScopeSettings
from .models import DocumentRecord, ScopeRecord


def visible_documents(
    scope: ScopeRecord,
    *,
    include_shared: bool = True,
    verified_versions: tuple[tuple[str, int], ...] | None = None,
) -> ColumnElement[bool]:
    # Legacy publication copies never become local or installation-visible memory.
    active = and_(DocumentRecord.state == "active", DocumentRecord.publication_source_id.is_(None))
    local = DocumentRecord.scope_id == scope.id
    if (
        not include_shared
        or scope.audience not in ("public", "private")
        or not ScopeSettings.model_validate(scope.settings_json).enabled
    ):
        return and_(active, local)
    source = aliased(ScopeRecord)
    verified_source = (
        tuple_(source.id, source.version).in_(verified_versions) if verified_versions is not None else true()
    )
    recipient_connected = exists(
        select(AccountTargetRecord.id).where(
            AccountTargetRecord.account_id == scope.account_id,
            AccountTargetRecord.target_kind == "conversation",
            AccountTargetRecord.external_target_id == scope.external_conversation_id,
        )
    )
    source_connected = exists(
        select(AccountTargetRecord.id)
        .where(
            AccountTargetRecord.account_id == source.account_id,
            AccountTargetRecord.target_kind == "conversation",
            AccountTargetRecord.external_target_id == source.external_conversation_id,
        )
        .correlate(source)
    )
    shared = exists(
        select(source.id)
        .where(
            source.id == DocumentRecord.scope_id,
            source.account_id == scope.account_id,
            source.organization_id == scope.organization_id,
            source.workspace_id == scope.workspace_id,
            source.provider_id == scope.provider_id,
            source.audience.in_(("public", "private")),
            source.settings_json["enabled"].as_boolean().is_(True),
            source.settings_json["visibility"].as_string() == "installation",
            source_connected,
            verified_source,
        )
        .correlate(DocumentRecord)
    )
    return and_(active, or_(local, and_(recipient_connected, shared)))
