"""One invitation lifecycle for administrator initialization and member onboarding."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.ids import new_object_id
from a13n_service.resource_keys import insert_with_key
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import utc_now

from ..audit import AuthenticationAuditActor, SystemAuditActor
from ..auth.credentials import expired
from ..auth.passwords import Passwords, matches_token, new_token, token_hash
from ..auth.sessions import Login, create_session, require_user
from ..authentication import AuthenticationError
from ..configuration import IdentityConfiguration
from ..domain import AuthenticatedActor
from ..models import (
    InvitationGrantRecord,
    InvitationRecord,
    OrganizationRecord,
    PasswordCredentialRecord,
    RoleBindingRecord,
    UserRecord,
    WorkspaceRecord,
)
from ..schemas import AcceptInvitationRequest, CreateInvitationRequest, Grant, Invitation, InvitationDelivery
from ..service_common import audit, identity_error, identity_transaction, lock_bootstrap, not_found, require_version
from .bindings import authorize_grants, authorize_inviter_grants, grant_role, validate_grants
from .mail import InvitationMailer


@dataclass(frozen=True, slots=True)
class IssuedInvitation:
    invitation: Invitation
    token: str = field(repr=False)


async def invitation_resources(session: AsyncSession, rows: Sequence[InvitationRecord]) -> list[Invitation]:
    if not rows:
        return []
    grants: dict[str, list[Grant]] = {row.id: [] for row in rows}
    for grant in await session.scalars(
        select(InvitationGrantRecord)
        .where(InvitationGrantRecord.invitation_id.in_(grants))
        .order_by(InvitationGrantRecord.resource_type, InvitationGrantRecord.resource_id)
    ):
        grants[grant.invitation_id].append(Grant.model_validate(grant, from_attributes=True))
    return [
        Invitation(
            id=row.id,
            organization_id=row.organization_id,
            email=row.email,
            verification_mode=row.verification_mode,
            expires_at=row.expires_at,
            accepted_at=row.accepted_at,
            revoked_at=row.revoked_at,
            created_by_user_id=row.created_by_user_id,
            version=row.version,
            created_at=row.created_at,
            updated_at=row.updated_at,
            grants=tuple(grants[row.id]),
        )
        for row in rows
    ]


async def invitation_resource(session: AsyncSession, row: InvitationRecord) -> Invitation:
    return (await invitation_resources(session, [row]))[0]


class InvitationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        configuration: IdentityConfiguration,
        passwords: Passwords,
        mailer: InvitationMailer | None,
    ) -> None:
        self._sessions = sessions
        self._configuration = configuration
        self._passwords = passwords
        self._mailer = mailer

    async def initialize(self, *, reissue: bool = False) -> IssuedInvitation | None:
        async with transaction(self._sessions) as session:
            await lock_bootstrap(session)
            organizations = (await session.scalars(select(OrganizationRecord).limit(2))).all()
            if len(organizations) > 1:
                raise RuntimeError("OSS identity requires exactly one Organization")
            if not organizations:
                if self._configuration.initial_admin_email is None:
                    raise RuntimeError("Set A13N_SERVICE_IAM_INITIAL_ADMIN_EMAIL to initialize OSS identity")
                now = utc_now()
                organization = OrganizationRecord(
                    id=new_object_id("org"), name="Organization", created_at=now, updated_at=now
                )
                await insert_with_key(session, organization, prefix="org")
                await insert_with_key(
                    session,
                    WorkspaceRecord(
                        id=new_object_id("ws"),
                        organization_id=organization.id,
                        name="default",
                        created_at=now,
                        updated_at=now,
                        deleted_at=None,
                    ),
                    prefix="workspace",
                )
                await session.flush()
            else:
                organization = organizations[0]
                await session.execute(
                    update(OrganizationRecord)
                    .where(OrganizationRecord.id == organization.id)
                    .values(name=OrganizationRecord.name)
                )
            row = await session.scalar(
                select(InvitationRecord).where(
                    InvitationRecord.organization_id == organization.id,
                    InvitationRecord.created_by_user_id.is_(None),
                )
            )
            if row is not None:
                if row.accepted_at is not None:
                    if reissue:
                        raise identity_error(
                            "initialization_complete", "Administrator initialization is already complete."
                        )
                    return None
                if not reissue:
                    return None
                issued = await self._renew(session, row)
            else:
                admin = await session.scalar(
                    select(RoleBindingRecord.id)
                    .where(
                        RoleBindingRecord.organization_id == organization.id,
                        RoleBindingRecord.resource_type == "organization",
                        RoleBindingRecord.principal_type == "user",
                        RoleBindingRecord.role_key == "admin",
                    )
                    .limit(1)
                )
                if admin is not None:
                    if reissue:
                        raise identity_error(
                            "initialization_complete", "Administrator initialization is already complete."
                        )
                    return None
                email = self._configuration.initial_admin_email
                if email is None:
                    raise RuntimeError("Set A13N_SERVICE_IAM_INITIAL_ADMIN_EMAIL to initialize OSS identity")
                issued = await self._issue(
                    session,
                    organization.id,
                    str(email),
                    [
                        Grant(resource_type="organization", resource_id=organization.id, role_key="admin"),
                    ],
                    creator=None,
                )
            audit(
                session,
                actor=SystemAuditActor(request_id=None),
                action="invitation.bootstrap",
                resource_type="invitation",
                resource_id=issued.invitation.id,
                organization_id=organization.id,
            )
            return issued

    async def create(
        self, actor: AuthenticatedActor, organization_id: str, request: CreateInvitationRequest
    ) -> InvitationDelivery:
        async with identity_transaction(self._sessions, organization_id) as session:
            user = await require_user(session, actor)
            await authorize_grants(session, actor, organization_id, request.grants)
            await validate_grants(session, organization_id, request.grants)
            issued = await self._issue(session, organization_id, str(request.email), request.grants, creator=user.id)
            audit(
                session,
                actor=actor,
                action="invitation.create",
                resource_type="invitation",
                resource_id=issued.invitation.id,
                organization_id=organization_id,
            )
        return await self.deliver(issued)

    async def resend(self, actor: AuthenticatedActor, invitation_id: str, expected_version: int) -> InvitationDelivery:
        organization_id = await self._organization(invitation_id)
        async with identity_transaction(self._sessions, organization_id) as session:
            row = await self._managed(session, actor, invitation_id)
            require_version(row.version, expected_version)
            if row.accepted_at is not None or row.revoked_at is not None:
                raise identity_error("invitation_closed", "The invitation is already closed.")
            issued = await self._renew(session, row)
            audit(
                session,
                actor=actor,
                action="invitation.resend",
                resource_type="invitation",
                resource_id=row.id,
                organization_id=row.organization_id,
            )
        return await self.deliver(issued)

    async def revoke(self, actor: AuthenticatedActor, invitation_id: str, expected_version: int) -> Invitation:
        organization_id = await self._organization(invitation_id)
        async with identity_transaction(self._sessions, organization_id) as session:
            row = await self._managed(session, actor, invitation_id)
            require_version(row.version, expected_version)
            if row.accepted_at is not None:
                raise identity_error("invitation_closed", "The invitation is already accepted.")
            if row.revoked_at is None:
                row.revoked_at = row.updated_at = utc_now()
                row.version += 1
                audit(
                    session,
                    actor=actor,
                    action="invitation.revoke",
                    resource_type="invitation",
                    resource_id=row.id,
                    organization_id=row.organization_id,
                )
            return await invitation_resource(session, row)

    async def accept(self, invitation_id: str, request: AcceptInvitationRequest, *, request_id: str | None) -> Login:
        token = request.token.get_secret_value()
        async with short_session(self._sessions) as session:
            row = await session.get(InvitationRecord, invitation_id)
            self._require_pending(row, token)
            assert row is not None
            organization_id, email = row.organization_id, row.normalized_email
            user = await session.scalar(select(UserRecord).where(UserRecord.normalized_email == email))
            user_id = None if user is None else user.id
            credential = None if user is None else await session.get(PasswordCredentialRecord, user.id)
            verifier = None if credential is None else credential.password_hash
        password = request.password.get_secret_value()
        if user_id is not None and not await self._passwords.verify(verifier, password):
            raise AuthenticationError("invalid credentials")
        new_hash = await self._passwords.hash(password) if user_id is None else None
        async with identity_transaction(self._sessions, organization_id) as session:
            row = await session.get(InvitationRecord, invitation_id)
            self._require_pending(row, token)
            assert row is not None
            resource = await invitation_resource(session, row)
            await validate_grants(session, organization_id, list(resource.grants))
            if row.created_by_user_id is not None:
                await authorize_inviter_grants(session, row.created_by_user_id, organization_id, list(resource.grants))
            current = await session.scalar(
                select(UserRecord).where(UserRecord.normalized_email == email).with_for_update()
            )
            now = utc_now()
            if user_id is None:
                if current is not None:
                    raise identity_error(
                        "invitation_retry_required", "Retry acceptance with the existing account's password."
                    )
                assert new_hash is not None
                user = UserRecord(
                    id=new_object_id("usr"),
                    email=row.email,
                    normalized_email=email,
                    name=request.name or row.email.split("@", 1)[0],
                    status="active",
                    email_verified_at=now if row.verification_mode == "email" else None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(user)
                await session.flush()
                session.add(
                    PasswordCredentialRecord(
                        user_id=user.id, password_hash=new_hash, password_changed_at=now, created_at=now
                    )
                )
            else:
                user = current
                credential = await session.get(PasswordCredentialRecord, user_id)
                if (
                    user is None
                    or user.id != user_id
                    or user.status != "active"
                    or credential is None
                    or credential.password_hash != verifier
                ):
                    raise AuthenticationError("invalid credentials")
                if row.verification_mode == "email":
                    user.email_verified_at = user.email_verified_at or now
            # Workspace-only invitations establish membership without inventing
            # an Organization grant the Workspace administrator could not author.
            grants = list(resource.grants)
            if not any(g.resource_type == "organization" for g in grants):
                grants.insert(0, Grant(resource_type="organization", resource_id=organization_id, role_key="member"))
            for grant in grants:
                await grant_role(
                    session,
                    organization_id=organization_id,
                    principal_type="user",
                    principal_id=user.id,
                    resource_type=grant.resource_type,
                    resource_id=grant.resource_id,
                    workspace_id=grant.resource_id if grant.resource_type == "workspace" else None,
                    role_key=grant.role_key,
                    created_by_user_id=row.created_by_user_id or user.id,
                    now=now,
                    additive=True,
                )
            row.accepted_at, row.accepted_by_user_id, row.updated_at = now, user.id, now
            row.version += 1
            audit(
                session,
                actor=AuthenticationAuditActor(user.id, "password", None, request_id),
                action="invitation.accept",
                resource_type="invitation",
                resource_id=row.id,
                organization_id=organization_id,
            )
            return create_session(session, user, session_days=self._configuration.session_days, request_id=request_id)

    async def deliver(self, issued: IssuedInvitation) -> InvitationDelivery:
        # Fragment tokens never reach HTTP access logs or Referer headers.
        url = f"{self._configuration.public_origin}/invitations/{issued.invitation.id}/accept#token={issued.token}"
        if self._mailer is None:
            return InvitationDelivery(invitation=issued.invitation, delivery="manual", invitation_url=url)
        sent = await self._mailer.send(issued.invitation.email, url)
        return InvitationDelivery(invitation=issued.invitation, delivery="sent" if sent else "failed")

    async def _issue(
        self, session: AsyncSession, organization_id: str, email: str, grants: list[Grant], *, creator: str | None
    ) -> IssuedInvitation:
        now, token = utc_now(), new_token()
        row = InvitationRecord(
            id=new_object_id("inv"),
            organization_id=organization_id,
            email=email,
            normalized_email=email.casefold(),
            token_hash=token_hash(token),
            verification_mode="email" if self._mailer else "out_of_band",
            expires_at=now + timedelta(days=self._configuration.invitation_days),
            accepted_at=None,
            accepted_by_user_id=None,
            revoked_at=None,
            created_by_user_id=creator,
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
        for grant in grants:
            session.add(
                InvitationGrantRecord(
                    invitation_id=row.id,
                    organization_id=organization_id,
                    resource_type=grant.resource_type,
                    resource_id=grant.resource_id,
                    workspace_id=grant.resource_id if grant.resource_type == "workspace" else None,
                    role_key=grant.role_key,
                )
            )
        await session.flush()
        return IssuedInvitation(await invitation_resource(session, row), token)

    async def _renew(self, session: AsyncSession, row: InvitationRecord) -> IssuedInvitation:
        token = new_token()
        row.token_hash = token_hash(token)
        row.verification_mode = "email" if self._mailer else "out_of_band"
        row.updated_at = utc_now()
        row.expires_at = row.updated_at + timedelta(days=self._configuration.invitation_days)
        row.revoked_at = None
        row.version += 1
        return IssuedInvitation(await invitation_resource(session, row), token)

    async def _organization(self, invitation_id: str) -> str:
        async with short_session(self._sessions) as session:
            organization_id = await session.scalar(
                select(InvitationRecord.organization_id).where(InvitationRecord.id == invitation_id)
            )
            if organization_id is None:
                raise not_found()
            return organization_id

    async def _managed(self, session: AsyncSession, actor: AuthenticatedActor, invitation_id: str) -> InvitationRecord:
        await require_user(session, actor)
        row = await session.get(InvitationRecord, invitation_id)
        if row is None or row.created_by_user_id is None:
            raise not_found()
        resource = await invitation_resource(session, row)
        await authorize_grants(session, actor, row.organization_id, list(resource.grants))
        return row

    @staticmethod
    def _require_pending(row: InvitationRecord | None, token: str) -> None:
        if (
            row is None
            or row.accepted_at is not None
            or row.revoked_at is not None
            or expired(row.expires_at)
            or not matches_token(token, row.token_hash)
        ):
            raise identity_error(
                "invitation_invalid", "The invitation is invalid or no longer available.", ErrorCategory.unauthenticated
            )
