"""Bounded IAM collections using the shared query-bound cursor codec."""

from pydantic import Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.collection_cursors import (
    CollectionCursorMismatchError,
    InvalidCollectionCursorError,
    decode_collection_cursor,
    encode_collection_cursor,
)
from a13n_service.storage import short_session

from .auth_models import ApiKeyRecord, AuthSessionRecord, InvitationGrantRecord, InvitationRecord
from .authorization import AuthenticatedActor, WorkspaceAction, authorize_organization_admin, authorize_workspace
from .invitations import invitation_resources
from .models import RoleBindingRecord, ServiceAccountRecord, UserRecord
from .schemas import ApiKey, AuthSession, Invitation, Page, RequestModel, Resource, RoleBinding, ServiceAccount, User
from .service_accounts import ServiceAccountService, account_resources
from .service_common import identity_error, not_found
from .sessions import require_user


class PageRequest(RequestModel):
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=2048)

    def after(self, scope: dict[str, object]) -> str:
        if self.cursor is None:
            return ""
        try:
            payload = decode_collection_cursor(self.cursor, scope=scope, kind="iam")
            identifier = payload.get("id")
            if not isinstance(identifier, str) or not identifier or len(identifier) > 72:
                raise InvalidCollectionCursorError
            return identifier
        except (InvalidCollectionCursorError, CollectionCursorMismatchError) as error:
            raise identity_error(
                "invalid_cursor", "The cursor does not match this collection.", ErrorCategory.invalid_request
            ) from error

    def finish[T: Resource](self, items: list[T], scope: dict[str, object]) -> Page[T]:
        more = len(items) > self.limit
        items = items[: self.limit]
        return Page(
            items=items,
            next_cursor=encode_collection_cursor({"id": items[-1].id}, scope=scope, kind="iam") if more else None,
        )


def query_scope(actor: AuthenticatedActor, collection: str, parent: str) -> dict[str, object]:
    return {
        "collection": collection,
        "parent": parent,
        "principal": actor.principal.model_dump(mode="json"),
        "workspace": actor.boundary_workspace_id,
        "organization": actor.boundary_organization_id,
    }


class IdentityCollections:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def sessions(self, actor: AuthenticatedActor, page: PageRequest) -> Page[AuthSession]:
        scope = query_scope(actor, "auth_sessions", actor.principal.principal_id)
        async with short_session(self._sessions) as session:
            user = await require_user(session, actor, browser=True)
            rows = await session.scalars(
                select(AuthSessionRecord)
                .where(
                    AuthSessionRecord.user_id == user.id,
                    AuthSessionRecord.id > page.after(scope),
                )
                .order_by(AuthSessionRecord.id)
                .limit(page.limit + 1)
            )
            return page.finish([AuthSession.model_validate(row) for row in rows], scope)

    async def keys(
        self, actor: AuthenticatedActor, workspace_id: str, page: PageRequest, *, account_id: str | None = None
    ) -> Page[ApiKey]:
        scope = query_scope(actor, "api_keys", f"{workspace_id}:{account_id or actor.principal.principal_id}")
        async with short_session(self._sessions) as session:
            user = await require_user(session, actor)
            if account_id is not None:
                await authorize_workspace(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.service_account_manage
                )
                await ServiceAccountService.require(session, actor, account_id)
            elif actor.boundary_workspace_id is not None and actor.boundary_workspace_id != workspace_id:
                raise not_found()
            rows = await session.scalars(
                select(ApiKeyRecord)
                .where(
                    ApiKeyRecord.principal_type == ("service_account" if account_id else "user"),
                    ApiKeyRecord.principal_id == (account_id or user.id),
                    ApiKeyRecord.boundary_id == workspace_id,
                    ApiKeyRecord.id > page.after(scope),
                )
                .order_by(ApiKeyRecord.id)
                .limit(page.limit + 1)
            )
            return page.finish([ApiKey.model_validate(row) for row in rows], scope)

    async def invitations(
        self, actor: AuthenticatedActor, organization_id: str, page: PageRequest, *, workspace_id: str | None = None
    ) -> Page[Invitation]:
        scope = query_scope(actor, "invitations", workspace_id or organization_id)
        async with short_session(self._sessions) as session:
            await require_user(session, actor)
            if workspace_id is None:
                if await authorize_organization_admin(session, actor=actor) != organization_id:
                    raise not_found()
            else:
                await authorize_workspace(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.invitation_manage
                )
            query = select(InvitationRecord).where(
                InvitationRecord.organization_id == organization_id,
                InvitationRecord.created_by_user_id.is_not(None),
                InvitationRecord.id > page.after(scope),
            )
            if workspace_id is not None:
                # A Workspace administrator cannot inspect an invitation granting
                # permissions in any other resource, even if one grant is local.
                foreign_grant = (
                    select(InvitationGrantRecord.invitation_id)
                    .where(
                        InvitationGrantRecord.invitation_id == InvitationRecord.id,
                        or_(
                            InvitationGrantRecord.resource_type != "workspace",
                            InvitationGrantRecord.resource_id != workspace_id,
                        ),
                    )
                    .exists()
                )
                query = query.where(~foreign_grant)
            rows = await session.scalars(query.order_by(InvitationRecord.id).limit(page.limit + 1))
            return page.finish(await invitation_resources(session, rows.all()), scope)

    async def accounts(self, actor: AuthenticatedActor, workspace_id: str, page: PageRequest) -> Page[ServiceAccount]:
        scope = query_scope(actor, "service_accounts", workspace_id)
        async with short_session(self._sessions) as session:
            await require_user(session, actor)
            await authorize_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.service_account_manage
            )
            rows = await session.scalars(
                select(ServiceAccountRecord)
                .where(
                    ServiceAccountRecord.workspace_id == workspace_id,
                    ServiceAccountRecord.deleted_at.is_(None),
                    ServiceAccountRecord.id > page.after(scope),
                )
                .order_by(ServiceAccountRecord.id)
                .limit(page.limit + 1)
            )
            return page.finish(await account_resources(session, rows.all()), scope)

    async def bindings(
        self, actor: AuthenticatedActor, organization_id: str, page: PageRequest, *, workspace_id: str | None = None
    ) -> Page[RoleBinding]:
        scope = query_scope(actor, "role_bindings", workspace_id or organization_id)
        async with short_session(self._sessions) as session:
            await require_user(session, actor)
            if workspace_id is None:
                if await authorize_organization_admin(session, actor=actor) != organization_id:
                    raise not_found()
            else:
                await authorize_workspace(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.role_binding_manage
                )
            rows = await session.scalars(
                select(RoleBindingRecord)
                .where(
                    RoleBindingRecord.organization_id == organization_id,
                    RoleBindingRecord.workspace_id == workspace_id,
                    RoleBindingRecord.resource_type == ("workspace" if workspace_id else "organization"),
                    RoleBindingRecord.id > page.after(scope),
                )
                .order_by(RoleBindingRecord.id)
                .limit(page.limit + 1)
            )
            return page.finish([RoleBinding.model_validate(row) for row in rows], scope)

    async def users(self, actor: AuthenticatedActor, organization_id: str, page: PageRequest) -> Page[User]:
        scope = query_scope(actor, "users", organization_id)
        async with short_session(self._sessions) as session:
            if await authorize_organization_admin(session, actor=actor) != organization_id:
                raise not_found()
            rows = await session.scalars(
                select(UserRecord)
                .join(RoleBindingRecord, RoleBindingRecord.principal_id == UserRecord.id)
                .where(
                    RoleBindingRecord.organization_id == organization_id,
                    RoleBindingRecord.resource_type == "organization",
                    RoleBindingRecord.resource_id == organization_id,
                    RoleBindingRecord.principal_type == "user",
                    UserRecord.id > page.after(scope),
                )
                .order_by(UserRecord.id)
                .limit(page.limit + 1)
            )
            return page.finish([User.model_validate(row) for row in rows], scope)
