"""Authorized HookSubscription management and Webhook redrive."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.durable_operations.outbox import redrive_outbox
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.iam import AuthenticatedActor, WorkspaceAction
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import require_aware_utc, utc_now

from .access import authorize_hook, validate_hook_scope
from .cursors import HookCursorError, decode_hook_cursor, encode_hook_cursor
from .domain import (
    CreateHookSubscriptionRequest,
    HookSubscription,
    HookSubscriptionCollection,
    UpdateHookSubscriptionRequest,
    UpdateHookSubscriptionStateRequest,
)
from .errors import HookManagementError
from .management_records import (
    hook_audit,
    hook_cursor_scope,
    management_error_from_invariant,
    replace_hook_configuration,
    require_hook_etag,
    require_hook_revision,
    touch_hook,
)
from .models import HookSubscriptionRecord, HookSubscriptionRevisionRecord
from .persistence import (
    HookSubscriptionInvariantError,
    create_hook_subscription,
    lock_hook_workspace,
    read_hook_subscriptions,
    require_active_workspace_secret,
    require_hook_capacity,
)
from .validation import HookEndpointValidationError, validate_hook_endpoint


class HookSubscriptionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        endpoint_policy: EndpointPolicy,
        *,
        clock: Callable[[], datetime] | None = None,
        validation_timeout_seconds: float = 10.0,
    ) -> None:
        if validation_timeout_seconds <= 0:
            raise ValueError("Hook endpoint validation timeout must be positive")
        self._sessions = sessions
        self._endpoint_policy = endpoint_policy
        self._clock = clock or utc_now
        self._validation_timeout_seconds = validation_timeout_seconds

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        request: CreateHookSubscriptionRequest,
    ) -> HookSubscription:
        await self._preauthorize_endpoint_change(
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.hook_subscription_create,
            endpoint_url=request.webhook.endpoint_url,
        )
        now = self._now()
        async with transaction(self._sessions) as database:
            workspace = await authorize_hook(
                database,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.hook_subscription_create,
            )
            await authorize_hook(
                database,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.secrets_bind,
            )
            await validate_hook_scope(
                database,
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
                request=request,
            )
            try:
                head = await create_hook_subscription(
                    database,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    actor_type=actor.principal.principal_type.value,
                    actor_id=actor.principal.principal_id,
                    subscription=request,
                    now=now,
                )
            except HookSubscriptionInvariantError as error:
                raise management_error_from_invariant(error) from error
            revision = await require_hook_revision(database, head.current_revision_id)
            database.add(hook_audit(actor, head, "hook_subscription.create", now))
            return head.to_resource(revision)

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
    ) -> HookSubscriptionCollection:
        if limit < 1 or limit > 100:
            raise HookManagementError("invalid_request", "limit must be between 1 and 100.", status_code=400)
        scope = hook_cursor_scope(actor, workspace_id)
        try:
            after = decode_hook_cursor(cursor, scope=scope) if cursor is not None else None
        except HookCursorError as error:
            raise HookManagementError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
        async with short_session(self._sessions) as database:
            workspace = await authorize_hook(
                database,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.hook_subscription_read,
            )
            rows = await read_hook_subscriptions(
                database,
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
                limit=limit + 1,
                after=after,
            )
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            next_cursor = encode_hook_cursor(
                updated_at=page[-1][0].updated_at,
                subscription_id=page[-1][0].id,
                scope=scope,
            )
        return HookSubscriptionCollection(
            items=tuple(head.to_resource(revision) for head, revision in page),
            next_cursor=next_cursor,
        )

    async def get(self, *, actor: AuthenticatedActor, subscription_id: str) -> HookSubscription:
        async with short_session(self._sessions) as database:
            head, revision = await self._authorized_subscription(
                database,
                actor=actor,
                subscription_id=subscription_id,
                action=WorkspaceAction.hook_subscription_read,
            )
            return head.to_resource(revision)

    async def update_configuration(
        self,
        *,
        actor: AuthenticatedActor,
        subscription_id: str,
        if_match: str,
        request: UpdateHookSubscriptionRequest,
    ) -> HookSubscription:
        await self._preauthorize_endpoint_change(
            actor=actor,
            workspace_id=actor.workspace_id,
            action=WorkspaceAction.hook_subscription_update,
            endpoint_url=request.webhook.endpoint_url,
        )
        now = self._now()
        async with transaction(self._sessions) as database:
            head, revision = await self._authorized_subscription(
                database,
                actor=actor,
                subscription_id=subscription_id,
                action=WorkspaceAction.hook_subscription_update,
                lock=True,
            )
            await authorize_hook(
                database,
                actor=actor,
                workspace_id=head.workspace_id,
                action=WorkspaceAction.secrets_bind,
            )
            require_hook_etag(head, if_match)
            await validate_hook_scope(
                database,
                organization_id=head.organization_id,
                workspace_id=head.workspace_id,
                request=request,
            )
            try:
                await require_active_workspace_secret(
                    database,
                    organization_id=head.organization_id,
                    workspace_id=head.workspace_id,
                    secret_id=request.webhook.signing_secret_id,
                )
            except HookSubscriptionInvariantError as error:
                raise management_error_from_invariant(error) from error
            if revision.configuration().model_dump(mode="json") == request.model_dump(mode="json"):
                return head.to_resource(revision)
            replacement = await replace_hook_configuration(
                database,
                head=head,
                actor=actor,
                request=request,
                now=now,
            )
            database.add(hook_audit(actor, head, "hook_subscription.update", now))
            return head.to_resource(replacement)

    async def update_state(
        self,
        *,
        actor: AuthenticatedActor,
        subscription_id: str,
        if_match: str,
        request: UpdateHookSubscriptionStateRequest,
    ) -> HookSubscription:
        now = self._now()
        async with transaction(self._sessions) as database:
            head, revision = await self._authorized_subscription(
                database,
                actor=actor,
                subscription_id=subscription_id,
                action=WorkspaceAction.hook_subscription_update,
                lock=True,
            )
            require_hook_etag(head, if_match)
            if head.enabled == request.enabled:
                return head.to_resource(revision)
            if request.enabled:
                try:
                    await require_hook_capacity(
                        database,
                        organization_id=head.organization_id,
                        workspace_id=head.workspace_id,
                    )
                except HookSubscriptionInvariantError as error:
                    raise management_error_from_invariant(error) from error
            head.enabled = request.enabled
            touch_hook(head, actor, now)
            database.add(hook_audit(actor, head, "hook_subscription.update", now))
            await database.flush()
            return head.to_resource(revision)

    async def delete(
        self,
        *,
        actor: AuthenticatedActor,
        subscription_id: str,
        if_match: str,
    ) -> None:
        now = self._now()
        async with transaction(self._sessions) as database:
            head, _ = await self._authorized_subscription(
                database,
                actor=actor,
                subscription_id=subscription_id,
                action=WorkspaceAction.hook_subscription_delete,
                lock=True,
            )
            require_hook_etag(head, if_match)
            head.enabled = False
            head.deleted_at = now
            touch_hook(head, actor, now)
            database.add(hook_audit(actor, head, "hook_subscription.delete", now))

    async def redrive(
        self,
        *,
        actor: AuthenticatedActor,
        subscription_id: str,
        delivery_id: str,
    ) -> None:
        now = self._now()
        workspace_id = actor.workspace_id
        async with transaction(self._sessions) as database:
            workspace = await authorize_hook(
                database,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.hook_subscription_redrive,
            )
            row = (
                await database.execute(
                    select(OutboxRecord, HookSubscriptionRevisionRecord)
                    .join(
                        HookSubscriptionRevisionRecord,
                        HookSubscriptionRevisionRecord.id == OutboxRecord.destination_ref,
                    )
                    .where(
                        OutboxRecord.id == delivery_id,
                        OutboxRecord.source_kind == "lifecycle_event",
                        OutboxRecord.destination_kind == "webhook",
                        OutboxRecord.status == "dead_lettered",
                        HookSubscriptionRevisionRecord.hook_subscription_id == subscription_id,
                        HookSubscriptionRevisionRecord.organization_id == workspace.organization_id,
                        HookSubscriptionRevisionRecord.workspace_id == workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise HookManagementError(
                    "hook_delivery_not_found",
                    "The Hook delivery was not found.",
                    status_code=404,
                )
            outbox, revision = row
            if not await redrive_outbox(
                database,
                outbox_id=outbox.id,
                source_kind="lifecycle_event",
                destination_kind="webhook",
                destination_ref=revision.id,
                redriven_at=now,
            ):
                raise HookManagementError(
                    "hook_delivery_not_found",
                    "The Hook delivery was not found.",
                    status_code=404,
                )
            head = await database.get(HookSubscriptionRecord, subscription_id)
            if head is None:
                raise HookManagementError(
                    "hook_subscription_not_found",
                    "The Hook subscription was not found.",
                    status_code=404,
                )
            database.add(hook_audit(actor, head, "hook_subscription.redrive", now))

    async def _authorized_subscription(
        self,
        database: AsyncSession,
        *,
        actor: AuthenticatedActor,
        subscription_id: str,
        action: WorkspaceAction,
        lock: bool = False,
    ) -> tuple[HookSubscriptionRecord, HookSubscriptionRevisionRecord]:
        workspace_id = actor.workspace_id
        workspace = await authorize_hook(
            database,
            actor=actor,
            workspace_id=workspace_id,
            action=action,
        )
        if lock:
            await lock_hook_workspace(
                database,
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
            )
        query = select(HookSubscriptionRecord).where(
            HookSubscriptionRecord.id == subscription_id,
            HookSubscriptionRecord.organization_id == workspace.organization_id,
            HookSubscriptionRecord.workspace_id == workspace_id,
            HookSubscriptionRecord.deleted_at.is_(None),
        )
        if lock:
            query = query.with_for_update()
        head = await database.scalar(query)
        if head is None:
            raise HookManagementError(
                "hook_subscription_not_found",
                "The Hook subscription was not found.",
                status_code=404,
            )
        return head, await require_hook_revision(database, head.current_revision_id)

    async def _preauthorize_endpoint_change(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        action: WorkspaceAction,
        endpoint_url: str,
    ) -> None:
        async with short_session(self._sessions) as database:
            await authorize_hook(database, actor=actor, workspace_id=workspace_id, action=action)
            await authorize_hook(
                database,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.secrets_bind,
            )
        await self._validate_endpoint(endpoint_url)

    async def _validate_endpoint(self, endpoint_url: str) -> None:
        try:
            await validate_hook_endpoint(
                self._endpoint_policy,
                endpoint_url,
                timeout_seconds=self._validation_timeout_seconds,
            )
        except HookEndpointValidationError as error:
            raise HookManagementError(
                "invalid_webhook_endpoint",
                "The Webhook endpoint is not allowed.",
                status_code=400,
            ) from error

    def _now(self) -> datetime:
        value = self._clock()
        try:
            return require_aware_utc(value)
        except ValueError as error:
            raise ValueError("Hook management clock must include a UTC offset") from error


__all__ = ["HookManagementError", "HookSubscriptionService"]
