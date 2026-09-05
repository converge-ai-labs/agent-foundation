"""Mutable Hook head bookkeeping shared by management commands."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id

from .domain import UpdateHookSubscriptionRequest
from .errors import HookManagementError
from .models import HookSubscriptionRecord, HookSubscriptionRevisionRecord
from .persistence import HookSubscriptionInvariantCode, HookSubscriptionInvariantError


async def require_hook_revision(
    database: AsyncSession,
    revision_id: str,
) -> HookSubscriptionRevisionRecord:
    revision = await database.get(HookSubscriptionRevisionRecord, revision_id)
    if revision is None:
        raise HookManagementError(
            "hook_subscription_invalid",
            "The Hook subscription is invalid.",
            category=ErrorCategory.internal,
        )
    return revision


async def replace_hook_configuration(
    database: AsyncSession,
    *,
    head: HookSubscriptionRecord,
    actor: AuthenticatedActor,
    request: UpdateHookSubscriptionRequest,
    now: datetime,
) -> HookSubscriptionRevisionRecord:
    replacement = HookSubscriptionRevisionRecord(
        id=new_object_id("hsubr"),
        organization_id=head.organization_id,
        workspace_id=head.workspace_id,
        hook_subscription_id=head.id,
        version=head.version + 1,
        hook_names=list(request.hook_names),
        session_id=request.session_id,
        thread_id=request.thread_id,
        run_id=request.run_id,
        endpoint_url=request.webhook.endpoint_url,
        signing_secret_id=request.webhook.signing_secret_id,
        signature_profile=request.webhook.signature_profile,
        created_by_type=actor.principal.principal_type.value,
        created_by_id=actor.principal.principal_id,
        created_at=now,
    )
    database.add(replacement)
    head.version = replacement.version
    head.current_revision_id = replacement.id
    touch_hook(head, actor, now)
    await database.flush()
    return replacement


def require_hook_etag(head: HookSubscriptionRecord, if_match: str) -> None:
    current = resource_etag(head.id, head.updated_at)
    if not etag_matches(if_match, current):
        raise HookManagementError(
            "precondition_failed",
            "The Hook subscription changed after it was read.",
            category=ErrorCategory.stale_version,
            details={"current_etag": current},
        )


def touch_hook(head: HookSubscriptionRecord, actor: AuthenticatedActor, now: datetime) -> None:
    head.updated_by_type = actor.principal.principal_type.value
    head.updated_by_id = actor.principal.principal_id
    head.updated_at = now


def hook_audit(
    actor: AuthenticatedActor,
    head: HookSubscriptionRecord,
    action: str,
    now: datetime,
) -> SecurityAuditRecord:
    return SecurityAuditRecord(
        id=new_object_id("aud"),
        organization_id=head.organization_id,
        workspace_id=head.workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        action=action,
        resource_type="hook_subscription",
        resource_id=head.id,
        auth_method=actor.auth_method,
        credential_id=actor.credential_id,
        outcome="success",
        occurred_at=now,
        request_id=actor.request_id,
        details=None,
    )


def hook_cursor_scope(actor: AuthenticatedActor, workspace_id: str) -> dict[str, object]:
    return {
        "resource": "hook_subscription",
        "workspace_id": workspace_id,
        "principal_type": actor.principal.principal_type.value,
        "principal_id": actor.principal.principal_id,
    }


def management_error_from_invariant(error: HookSubscriptionInvariantError) -> HookManagementError:
    if error.code is HookSubscriptionInvariantCode.subscription_limit:
        return HookManagementError(
            "hook_subscription_limit_exceeded",
            "The active Hook subscription limit was exceeded.",
            category=ErrorCategory.conflict,
        )
    if error.code is HookSubscriptionInvariantCode.secret_unavailable:
        return HookManagementError(
            "hook_signing_secret_unavailable",
            "The selected Hook signing Secret is unavailable.",
            category=ErrorCategory.invalid_request,
        )
    if error.code is HookSubscriptionInvariantCode.workspace_unavailable:
        return HookManagementError(
            "resource_not_found",
            "The requested resource was not found.",
            category=ErrorCategory.not_found,
        )
    return HookManagementError(
        "hook_subscription_invalid",
        "The Hook subscription operation violated a persisted invariant.",
        category=ErrorCategory.internal,
    )


__all__ = [
    "hook_audit",
    "hook_cursor_scope",
    "management_error_from_invariant",
    "replace_hook_configuration",
    "require_hook_etag",
    "require_hook_revision",
    "touch_hook",
]
