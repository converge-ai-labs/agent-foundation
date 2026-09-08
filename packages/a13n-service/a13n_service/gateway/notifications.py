"""Native notification application service."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_agent_scoped_collection,
    authorize_workspace,
)
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.storage import short_session

NotificationTopic = Literal[
    "thread.updated",
    "run.updated",
    "pending_action.updated",
    "session.updated",
]


class NotificationError(ApplicationError):
    """Safe notification subscription failure."""


class NotificationSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subscription_id: str = Field(min_length=1, max_length=128)
    scope: Literal["thread", "workspace"]
    resource_id: str = Field(min_length=1, max_length=72)
    topics: tuple[NotificationTopic, ...] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class AuthorizedNotificationSubscription:
    definition: NotificationSubscription
    organization_id: str
    workspace_id: str
    visible_agent_ids: frozenset[str] | None
    after_seq: int


@dataclass(frozen=True, slots=True)
class NotificationFact:
    seq: int
    resource_type: str
    resource_id: str
    resource_version: int | None
    workspace_id: str
    session_id: str | None
    thread_id: str | None
    run_id: str | None
    occurred_at: datetime
    event_type: str


class NotificationService:
    """Authorize subscription sets and read best-effort wake-up facts."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def authorize(
        self,
        *,
        actor: AuthenticatedActor,
        subscriptions: tuple[NotificationSubscription, ...],
    ) -> tuple[AuthorizedNotificationSubscription, ...]:
        if len({item.subscription_id for item in subscriptions}) != len(subscriptions):
            raise NotificationError(
                "duplicate_subscription_id",
                "Subscription IDs must be unique.",
                category=ErrorCategory.invalid_request,
            )
        async with short_session(self._sessions) as database:
            return tuple(
                [await self._authorize_one(database, actor=actor, subscription=item) for item in subscriptions]
            )

    async def read(
        self,
        subscription: AuthorizedNotificationSubscription,
        *,
        limit: int,
    ) -> tuple[NotificationFact, ...]:
        definition = subscription.definition
        async with short_session(self._sessions) as database:
            query = (
                select(LifecycleEventRecord, SessionRecord.workspace_id)
                .join(
                    SessionRecord,
                    and_(
                        SessionRecord.organization_id == LifecycleEventRecord.organization_id,
                        SessionRecord.id == LifecycleEventRecord.session_id,
                    ),
                )
                .join(
                    RunRecord,
                    and_(
                        RunRecord.organization_id == LifecycleEventRecord.organization_id,
                        RunRecord.id == LifecycleEventRecord.run_id,
                    ),
                )
                .where(
                    LifecycleEventRecord.organization_id == subscription.organization_id,
                    SessionRecord.workspace_id == subscription.workspace_id,
                    LifecycleEventRecord.seq > subscription.after_seq,
                )
                .order_by(LifecycleEventRecord.seq)
                .limit(limit)
            )
            if definition.scope == "thread":
                query = query.where(LifecycleEventRecord.thread_id == definition.resource_id)
            if subscription.visible_agent_ids is not None:
                query = query.where(RunRecord.agent_id.in_(subscription.visible_agent_ids))
            rows = (await database.execute(query)).all()
        return tuple(
            NotificationFact(
                seq=record.seq,
                resource_type=record.entity_type,
                resource_id=record.entity_id,
                resource_version=record.entity_version,
                workspace_id=workspace_id,
                session_id=record.session_id,
                thread_id=record.thread_id,
                run_id=record.run_id,
                occurred_at=record.occurred_at,
                event_type=record.event_type,
            )
            for record, workspace_id in rows
        )

    async def reauthorize(
        self,
        *,
        actor: AuthenticatedActor,
        subscription: AuthorizedNotificationSubscription,
    ) -> AuthorizedNotificationSubscription:
        async with short_session(self._sessions) as database:
            current = await self._authorize_one(
                database,
                actor=actor,
                subscription=subscription.definition,
            )
        return replace(current, after_seq=subscription.after_seq)

    async def _authorize_one(
        self,
        database: AsyncSession,
        *,
        actor: AuthenticatedActor,
        subscription: NotificationSubscription,
    ) -> AuthorizedNotificationSubscription:
        if subscription.scope == "thread" and "session.updated" in subscription.topics:
            raise NotificationError(
                "invalid_subscription_topic",
                "session.updated is not available for Thread subscriptions.",
                category=ErrorCategory.invalid_request,
            )
        try:
            if subscription.scope == "workspace":
                workspace_id = subscription.resource_id
                notification_scope = await authorize_agent_scoped_collection(
                    database,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.notification_subscribe,
                )
                for action in _topic_actions(subscription.topics):
                    await authorize_agent_scoped_collection(
                        database,
                        actor=actor,
                        workspace_id=workspace_id,
                        action=action,
                    )
                organization_id = notification_scope.workspace.organization_id
                visible_agent_ids = notification_scope.visible_agent_ids
            else:
                row = (
                    await database.execute(
                        select(ThreadRecord, SessionRecord.workspace_id)
                        .join(
                            SessionRecord,
                            and_(
                                SessionRecord.organization_id == ThreadRecord.organization_id,
                                SessionRecord.id == ThreadRecord.session_id,
                            ),
                        )
                        .where(
                            ThreadRecord.id == subscription.resource_id,
                            SessionRecord.workspace_id == actor.boundary_workspace_id
                            if actor.boundary_workspace_id is not None
                            else SessionRecord.organization_id == actor.boundary_organization_id,
                        )
                    )
                ).one_or_none()
                if row is None:
                    raise AuthorizationError("thread_not_found", concealed=True)
                thread, workspace_id = row
                run = await database.scalar(
                    select(RunRecord).where(
                        RunRecord.organization_id == thread.organization_id,
                        RunRecord.id == thread.current_run_id,
                    )
                )
                if run is None and thread.current_run_id is not None:
                    raise AuthorizationError("run_not_found", concealed=True)
                for action in {WorkspaceAction.notification_subscribe, *_topic_actions(subscription.topics)}:
                    if run is None:
                        await authorize_workspace(database, actor=actor, workspace_id=workspace_id, action=action)
                    else:
                        await authorize_agent(
                            database,
                            actor=actor,
                            workspace_id=workspace_id,
                            agent_id=run.agent_id,
                            action=action,
                        )
                organization_id = thread.organization_id
                visible_agent_ids = frozenset({run.agent_id}) if run else None
            high = await database.scalar(
                select(func.max(LifecycleEventRecord.seq)).where(
                    LifecycleEventRecord.organization_id == organization_id
                )
            )
        except AuthorizationError as error:
            raise NotificationError(
                "resource_not_found",
                "The requested subscription resource was not found.",
                category=ErrorCategory.not_found,
            ) from error
        return AuthorizedNotificationSubscription(
            definition=subscription,
            organization_id=organization_id,
            workspace_id=workspace_id,
            visible_agent_ids=visible_agent_ids,
            after_seq=high or 0,
        )


def _topic_actions(topics: tuple[NotificationTopic, ...]) -> frozenset[WorkspaceAction]:
    actions: set[WorkspaceAction] = set()
    if "thread.updated" in topics:
        actions.add(WorkspaceAction.thread_read)
    if {"run.updated", "pending_action.updated"}.intersection(topics):
        actions.add(WorkspaceAction.run_read)
    if "session.updated" in topics:
        actions.add(WorkspaceAction.session_read)
    return frozenset(actions)


__all__ = [
    "AuthorizedNotificationSubscription",
    "NotificationError",
    "NotificationFact",
    "NotificationService",
    "NotificationSubscription",
    "NotificationTopic",
]
