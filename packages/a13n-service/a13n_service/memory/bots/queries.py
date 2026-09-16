"""SQL visibility predicates shared by navigation, reads, and search planning."""

from sqlalchemy import Select, and_, exists, or_, select, true, tuple_
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from .models import (
    DocumentRecord,
    PublicationRecipientRecord,
    ScopeRecord,
    SharingParticipantRecord,
    SharingPolicyRecord,
)


def visible_documents(
    scope: ScopeRecord,
    *,
    include_shared: bool = True,
    verified_versions: tuple[tuple[str, int], ...] | None = None,
) -> ColumnElement[bool]:
    local = and_(DocumentRecord.scope_id == scope.id, DocumentRecord.publication_source_id.is_(None))
    if not include_shared or scope.audience not in ("public", "private"):
        return and_(DocumentRecord.state == "active", local)
    source = aliased(DocumentRecord)
    source_scope = aliased(ScopeRecord)
    verified_source = (
        tuple_(source_scope.id, source_scope.version).in_(verified_versions)
        if verified_versions is not None
        else true()
    )
    publication = exists(
        select(PublicationRecipientRecord.document_id)
        .join(source, source.id == DocumentRecord.publication_source_id)
        .join(source_scope, source_scope.id == source.scope_id)
        .where(
            PublicationRecipientRecord.document_id == DocumentRecord.id,
            PublicationRecipientRecord.scope_id == scope.id,
            source.state == "active",
            source_scope.account_id == scope.account_id,
            source_scope.provider_id == scope.provider_id,
            source_scope.audience.in_(("public", "private")),
            source_scope.settings_json["enabled"].as_boolean().is_(True),
            verified_source,
        )
        .correlate(DocumentRecord)
    )
    mutual = exists(matching_policies(scope, verified_versions=verified_versions))
    return and_(DocumentRecord.state == "active", or_(local, publication, mutual))


def matching_policies(
    scope: ScopeRecord, *, verified_versions: tuple[tuple[str, int], ...] | None = None
) -> Select[tuple[SharingPolicyRecord]]:
    """The same policy predicates govern both access and its detail explanation."""
    source_scope = aliased(ScopeRecord)
    verified_source = (
        tuple_(source_scope.id, source_scope.version).in_(verified_versions)
        if verified_versions is not None
        else true()
    )
    origin = aliased(SharingParticipantRecord)
    recipient = aliased(SharingParticipantRecord)
    policy = SharingPolicyRecord
    # JSON kinds are a two-value schema; cast-free containment uses explicit positions.
    kind_matches = or_(
        policy.kinds_json[0].as_string() == DocumentRecord.kind, policy.kinds_json[1].as_string() == DocumentRecord.kind
    )
    return (
        select(policy)
        .join(origin, origin.policy_id == policy.id)
        .join(recipient, recipient.policy_id == policy.id)
        .join(source_scope, source_scope.id == origin.scope_id)
        .where(
            policy.account_id == scope.account_id,
            policy.provider_id == scope.provider_id,
            policy.enabled.is_(True),
            origin.scope_id == DocumentRecord.scope_id,
            recipient.scope_id == scope.id,
            source_scope.audience.in_(("public", "private")),
            source_scope.settings_json["enabled"].as_boolean().is_(True),
            verified_source,
            kind_matches,
            DocumentRecord.publication_source_id.is_(None),
            or_(
                policy.include_history.is_(True),
                and_(
                    DocumentRecord.saved_at >= policy.future_since,
                    DocumentRecord.saved_at >= origin.joined_at,
                    DocumentRecord.saved_at >= recipient.joined_at,
                ),
            ),
        )
        .correlate(DocumentRecord)
    )
