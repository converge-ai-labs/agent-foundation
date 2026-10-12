"""Invitations: pending grants for an email address, sent as one-use links and accepted by their token.

Only an administrator's login session creates or resends one: the login an accepted link starts would outlive
an API key's expiry and revocation. Acceptance is a public flow bound to the invited address: a new user sets a
password, an existing user proves theirs, and either way the grant is created under the inviter's current
authority and a login session starts. Archiving a workspace revokes its pending invitations under the
organization lock acceptance takes too, so no invitation grants a role in an archived workspace.
"""

import hmac
from datetime import timedelta

from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.audit import Scoped, record
from a13n_service.infra.crypto import KeyRing, secret_hash
from a13n_service.infra.db import Storage, lock, now, short_session, transaction, violated_constraint
from a13n_service.infra.errors import ServiceError, conflict, invalid, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.settings import Settings
from a13n_service.tenancy.access import (
    Access,
    AdminPath,
    administering,
    lock_member,
    lock_organization,
    principal_for,
    require_login_session,
)
from a13n_service.tenancy.authenticate import Login, open_session, session_csrf
from a13n_service.tenancy.authorize import Principal, Scope, allowed_verbs
from a13n_service.tenancy.credentials import hash_password, new_secret, set_password, verify_password
from a13n_service.tenancy.grants import add_grant, replace_grant
from a13n_service.tenancy.mail import Mail, link, queue_mail
from a13n_service.tenancy.schemas import (
    Invitation,
    InvitationAccept,
    InvitationCreate,
    InvitationPage,
    InvitationReceipt,
    NewPassword,
)
from a13n_service.tenancy.tables import GrantRow, InvitationRow, PasswordRow, PrincipalRow

_NEW_PASSWORD = TypeAdapter(NewPassword)
_WRONG_PASSWORD = "Incorrect password for the existing account of the invited email"


async def list_invitations(
    storage: Storage, access: Access, actor: Principal, path: AdminPath, *, limit: int, cursor: str | None
) -> InvitationPage:
    async with administering(storage, access, actor, path, action="invitation.list", reading=True) as (
        session,
        scope,
    ):
        rows, next_cursor = await cursors.id_page(
            session,
            select(InvitationRow).where(
                InvitationRow.organization_id == scope.organization_id,
                InvitationRow.workspace_id.is_not_distinct_from(scope.workspace_id),
            ),
            InvitationRow.id,
            kind="invitations",
            owner=scope.workspace_id or scope.organization_id,
            cursor=cursor,
            limit=limit,
        )
    return InvitationPage(items=[Invitation.model_validate(row) for row in rows], next_cursor=next_cursor)


async def create_invitation(
    storage: Storage,
    access: Access,
    keys: KeyRing,
    settings: Settings,
    actor: Principal,
    path: AdminPath,
    body: InvitationCreate,
) -> InvitationReceipt:
    require_login_session(actor)
    token = new_secret()
    async with administering(storage, access, actor, path, action="invitation.create") as (session, scope):
        access.check_role(body.role)
        pending = await session.scalar(
            select(InvitationRow.id)
            .where(
                InvitationRow.organization_id == scope.organization_id,
                InvitationRow.workspace_id.is_not_distinct_from(scope.workspace_id),
                InvitationRow.email == body.email,
                InvitationRow.accepted_at.is_(None),
                InvitationRow.revoked_at.is_(None),
                InvitationRow.expires_at > func.clock_timestamp(),
            )
            .limit(1)
        )
        if pending is not None:
            raise ServiceError(
                "already_exists", "A pending invitation exists; resend it", {"kind": "invitation", "key": body.email}
            )
        row = InvitationRow(
            id=new_object_id("inv"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            email=body.email,
            role=body.role,
            token_hash=secret_hash(token),
            invited_by_id=actor.id,
            expires_at=await now(session) + timedelta(seconds=settings.auth.invitation_seconds),
        )
        session.add(row)
        await session.flush()
        record(
            session,
            scope,
            actor_id=actor.id,
            action="invitation.create",
            target_kind="invitation",
            target_id=row.id,
            details={"email": row.email, "role": row.role},
        )
        return await _send(session, keys, settings, row, token)


async def resend_invitation(
    storage: Storage,
    access: Access,
    keys: KeyRing,
    settings: Settings,
    actor: Principal,
    path: AdminPath,
    invitation_id: str,
    *,
    if_match: str | None,
) -> InvitationReceipt:
    """Replace the link and renew the expiry; the previous link stops working."""
    require_login_session(actor)
    token = new_secret()
    async with administering(storage, access, actor, path, action="invitation.resend") as (session, scope):
        row = await _lock_pending(session, scope, invitation_id, if_match)
        row.token_hash = secret_hash(token)
        row.expires_at = await now(session) + timedelta(seconds=settings.auth.invitation_seconds)
        await session.flush()
        record(
            session, scope, actor_id=actor.id, action="invitation.resend", target_kind="invitation", target_id=row.id
        )
        return await _send(session, keys, settings, row, token)


async def revoke_invitation(
    storage: Storage, access: Access, actor: Principal, path: AdminPath, invitation_id: str, *, if_match: str | None
) -> Invitation:
    """Withdraw a pending invitation; like removing a grant, this is allowed in an archived workspace too."""
    async with administering(storage, access, actor, path, action="invitation.revoke", require_active=False) as (
        session,
        scope,
    ):
        row = await _lock_pending(session, scope, invitation_id, if_match)
        row.revoked_at = await now(session)
        await session.flush()
        record(
            session, scope, actor_id=actor.id, action="invitation.revoke", target_kind="invitation", target_id=row.id
        )
        return Invitation.model_validate(row)


async def accept_invitation(
    storage: Storage, access: Access, settings: Settings, invitation_id: str, body: InvitationAccept
) -> Login:
    async with short_session(storage) as session:
        invited = _verified(await session.get(InvitationRow, invitation_id), invitation_id, body.token)
        existing = (
            await session.execute(
                select(PrincipalRow.id, PasswordRow.hash)
                .outerjoin(PasswordRow, PasswordRow.principal_id == PrincipalRow.id)
                .where(PrincipalRow.email == invited.email)
            )
        ).one_or_none()
    password = body.password.get_secret_value()
    if existing is not None:
        # An existing account joins by proving its own password; the token proves the address.
        if not await verify_password(existing.hash, password):
            raise ServiceError("unauthenticated", _WRONG_PASSWORD)
        new_hash = None
    else:
        try:
            _NEW_PASSWORD.validate_python(password)
        except ValidationError:
            raise invalid("password", "too_short") from None
        new_hash = await hash_password(password)
    try:
        async with transaction(storage) as session:
            await lock_organization(session, invited.organization_id)
            row = _verified(await lock(session, InvitationRow, invitation_id), invitation_id, body.token)
            current = await now(session)
            if row.accepted_at is not None or row.revoked_at is not None:
                raise conflict("invitation", row.id, "accepted" if row.accepted_at is not None else "revoked")
            if row.expires_at <= current:
                raise conflict("invitation", row.id, "expired")
            if existing is not None:
                principal = await lock_member(session, existing.id)
                stored = await lock(session, PasswordRow, existing.id)
                if principal.status != "active" or stored is None or stored.hash != existing.hash:
                    raise ServiceError("unauthenticated", _WRONG_PASSWORD)
            else:
                assert new_hash is not None
                principal = PrincipalRow(
                    id=new_object_id("usr"), kind="user", name=body.name or row.email.split("@")[0], email=row.email
                )
                session.add(principal)
                await session.flush()
                await set_password(session, principal.id, new_hash)
            await _grant(session, access, row, principal)
            row.accepted_at, row.principal_id = current, principal.id
            await session.flush()
            record(
                session,
                Scope(row.organization_id, row.workspace_id),
                actor_id=principal.id,
                action="invitation.accept",
                target_kind="invitation",
                target_id=row.id,
                details={"principal_id": principal.id},
            )
            secret = await open_session(session, principal.id, seconds=settings.auth.session_seconds)
            accepted = await principal_for(session, access, principal.id)
    except IntegrityError as error:
        if violated_constraint(error) == "uq_principals_email":
            raise conflict("invitation", invitation_id, "email_registered") from None
        raise
    return Login(accepted, secret, session_csrf(secret))


async def _grant(session: AsyncSession, access: Access, row: InvitationRow, principal: PrincipalRow) -> None:
    """Grant the invited role while the inviter still administers the scope; it replaces any grant held there."""
    scope = Scope(row.organization_id, row.workspace_id)
    try:
        inviter = await principal_for(session, access, row.invited_by_id, share=True)
    except ServiceError:
        inviter = None  # disabled, or holding a role no longer defined
    if inviter is None or "admin" not in allowed_verbs(inviter, scope):
        raise conflict("invitation", row.id, "inviter_lost_authority")
    provenance: dict[str, JsonValue] = {"invitation_id": row.id, "invited_by_id": row.invited_by_id}
    held = await session.scalar(
        select(GrantRow)
        .where(
            GrantRow.principal_id == principal.id,
            GrantRow.organization_id == scope.organization_id,
            GrantRow.workspace_id.is_not_distinct_from(scope.workspace_id),
        )
        .with_for_update()
    )
    if held is None:
        await add_grant(session, principal, scope, row.role, actor_id=principal.id, details=provenance)
    elif held.role != row.role:
        await replace_grant(session, access, held, principal, row.role, actor_id=principal.id, details=provenance)


def _verified(row: InvitationRow | None, invitation_id: str, token: str) -> InvitationRow:
    if row is None or not hmac.compare_digest(row.token_hash, secret_hash(token)):
        raise not_found("invitation", invitation_id)
    return row


async def _lock_pending(
    session: AsyncSession, scope: Scoped, invitation_id: str, if_match: str | None
) -> InvitationRow:
    row = await lock(session, InvitationRow, invitation_id)
    if row is None or (row.organization_id, row.workspace_id) != (scope.organization_id, scope.workspace_id):
        raise not_found("invitation", invitation_id)
    require_match(if_match, row.id, row.version)
    if row.accepted_at is not None or row.revoked_at is not None:
        raise conflict("invitation", row.id, "accepted" if row.accepted_at is not None else "revoked")
    return row


async def _send(
    session: AsyncSession, keys: KeyRing, settings: Settings, row: InvitationRow, token: str
) -> InvitationReceipt:
    url = link(settings.server.public_url, f"/invitations/{row.id}/accept", token)
    mail = Mail(
        to=row.email,
        subject="You are invited to a13n",
        text=f"You have been invited to a13n. Accept the invitation with this single-use link:\n\n{url}\n\n"
        f"The link expires at {row.expires_at.isoformat()}.\n",
    )
    queued = queue_mail(
        session,
        keys,
        settings.auth.mail,
        mail,
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        purpose="invitation",
    )
    return InvitationReceipt(
        invitation=Invitation.model_validate(row),
        delivery="queued" if queued else "manual",
        invitation_url=None if queued else url,
    )
