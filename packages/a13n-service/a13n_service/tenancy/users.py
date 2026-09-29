"""User accounts: the caller's profile and profile image, email change, password change and reset, login sessions
and disabling, and the deployment operator's enable/disable switch.

Account changes need an account credential (a login session or an equivalent replacement authenticator);
a workspace API key can read the profile but never change the account behind it. Every change of the
password or email address ends the account's other login sessions and outstanding links.
"""

from typing import Literal

from a13n_logging import get_logger
from pydantic import JsonValue, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors, images
from a13n_service.infra.audit import record
from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, assign, lock, now, short_session, transaction, unique_key
from a13n_service.infra.errors import ServiceError, conflict, invalid, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.objects.interface import ObjectStore
from a13n_service.settings import Settings
from a13n_service.tenancy.access import Access, lock_organization, require_login_session
from a13n_service.tenancy.authorize import Principal, Scope, allowed_verbs
from a13n_service.tenancy.credentials import (
    consume_link,
    find_link,
    hash_password,
    issue_link,
    revoke_tokens,
    set_password,
    verify_password,
)
from a13n_service.tenancy.grants import other_administrator
from a13n_service.tenancy.mail import Mail, link, queue_mail
from a13n_service.tenancy.principals import avatar_url
from a13n_service.tenancy.schemas import (
    AccountDisable,
    LoginSession,
    LoginSessionPage,
    PasswordChange,
    PasswordResetConfirm,
    Profile,
    ProfileUpdate,
)
from a13n_service.tenancy.tables import GrantRow, PasswordRow, PrincipalRow, TokenRow

logger = get_logger(__name__)


def profile_view(row: PrincipalRow) -> Profile:
    return Profile(
        id=row.id,
        kind=row.kind,
        name=row.name,
        email=row.email,
        status=row.status,
        image_url=avatar_url(row),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def get_profile(storage: Storage, actor: Principal) -> Profile:
    async with short_session(storage) as session:
        row = await session.get(PrincipalRow, actor.id)
        if row is None:
            raise not_found("principal", actor.id)
        return profile_view(row)


async def update_profile(
    storage: Storage, keys: KeyRing, settings: Settings, actor: Principal, body: ProfileUpdate, *, if_match: str | None
) -> Profile:
    """Rename directly; a new email address takes effect only when its one-use link is confirmed."""
    require_login_session(actor)
    stored = await _verify_current(storage, actor, body.current_password) if body.email is not None else None
    if body.email is not None and settings.auth.mail.smtp_host is None:
        raise ServiceError("unavailable", "Email delivery is not configured", {"dependency": "mail"})
    async with transaction(storage) as session:
        row = await _current_profile(session, actor.id, if_match)
        changed = assign(row, body.model_dump(include={"name"}, exclude_none=True))
        if body.email is not None and body.email != row.email:
            await _check_password_unchanged(session, actor.id, stored)
            # Whether the address is taken is decided only at confirmation, so this reveals nothing about it.
            secret = await issue_link(
                session, row.id, "email_change", seconds=settings.auth.link_seconds, data={"new_email": body.email}
            )
            url = link(settings.server.public_url, "/confirm-email", secret)
            text = f"Confirm your new email address with this link:\n\n{url}\n"
            mail = Mail(body.email, "Confirm your a13n email address", text)
            _queue_account_mail(session, keys, settings, mail, purpose="email_change")
            changed.append("email_change_requested")
        await _record_update(session, row, changed)
        return profile_view(row)


async def change_avatar(
    storage: Storage, objects: ObjectStore, actor: Principal, data: bytes | None, *, if_match: str | None
) -> Profile:
    """Replace the caller's profile image with `data`, or remove it with None; bytes are stored only while the
    caller's ETag is current."""
    require_login_session(actor)
    image = None
    if data is not None:
        async with short_session(storage) as session:
            current = (await _current_profile(session, actor.id, if_match, locked=False)).image
        image = await images.store(objects, images.prefix_for(actor.id, organization_id=None), data, current=current)
    async with transaction(storage) as session:
        row = await _current_profile(session, actor.id, if_match)
        await _record_update(session, row, assign(row, {"image": image}))
        return profile_view(row)


async def get_avatar(storage: Storage, actor: Principal, user_id: str) -> dict[str, JsonValue] | None:
    """A user's profile image, for the user and for anyone who can read an organization the user belongs to;
    anyone else finds no such user."""
    async with short_session(storage) as session:
        row = await session.get(PrincipalRow, user_id)
        organizations = await session.scalars(select(GrantRow.organization_id).where(GrantRow.principal_id == user_id))
        shared = any("read" in allowed_verbs(actor, Scope(organization)) for organization in organizations)
    if row is None or row.kind != "user" or (row.id != actor.id and not shared):
        raise not_found("user", user_id)
    return row.image


async def confirm_email_change(storage: Storage, token: str) -> None:
    async with transaction(storage) as session:
        consumed = await consume_link(session, "email_change", token)
        principal = await lock(session, PrincipalRow, consumed.principal_id) if consumed else None
        if consumed is None or principal is None or principal.status != "active":
            raise invalid("token", "invalid_or_expired")
        email = str((consumed.data or {})["new_email"])
        principal.email = email
        with unique_key("user", "uq_principals_email", email):
            await session.flush()
        await revoke_tokens(session, principal.id)
        record(
            session,
            None,
            actor_id=principal.id,
            action="user.email.change",
            target_kind="user",
            target_id=principal.id,
        )


async def change_password(
    storage: Storage, actor: Principal, body: PasswordChange, *, current_session_id: str | None
) -> None:
    """Replace the password; every other login session and outstanding link ends."""
    require_login_session(actor)
    stored = await _verify_current(storage, actor, body.current_password)
    new_hash = await hash_password(body.password.get_secret_value())
    async with transaction(storage) as session:
        await _check_password_unchanged(session, actor.id, stored)
        await set_password(session, actor.id, new_hash)
        await revoke_tokens(session, actor.id, keep=current_session_id)
        record(session, None, actor_id=actor.id, action="user.password.change", target_kind="user", target_id=actor.id)


async def request_password_reset(storage: Storage, keys: KeyRing, settings: Settings, email: str) -> None:
    """Send a reset link when the address belongs to an active user; the caller learns nothing either way."""
    if settings.auth.mail.smtp_host is None:
        logger.info("Password reset requested without email delivery")
        return
    async with transaction(storage) as session:
        principal = await session.scalar(
            select(PrincipalRow).where(PrincipalRow.email == email, PrincipalRow.status == "active")
        )
        if principal is None:
            return
        secret = await issue_link(session, principal.id, "password_reset", seconds=settings.auth.link_seconds)
        url = link(settings.server.public_url, "/reset-password", secret)
        text = f"Reset your password with this single-use link:\n\n{url}\n"
        mail = Mail(email, "Reset your a13n password", text)
        _queue_account_mail(session, keys, settings, mail, purpose="password_reset")
        record(
            session,
            None,
            actor_id=None,
            action="user.password.reset_request",
            target_kind="user",
            target_id=principal.id,
        )


async def confirm_password_reset(storage: Storage, body: PasswordResetConfirm) -> None:
    """Consume the link and set the password; every login session and outstanding link of the account ends."""
    async with short_session(storage) as session:
        found = await find_link(session, "password_reset", body.token)
    if found is None:
        raise invalid("token", "invalid_or_expired")
    new_hash = await hash_password(body.password.get_secret_value())
    async with transaction(storage) as session:
        consumed = await consume_link(session, "password_reset", body.token)
        principal = await lock(session, PrincipalRow, consumed.principal_id) if consumed else None
        if consumed is None or principal is None or principal.status != "active":
            raise invalid("token", "invalid_or_expired")
        await set_password(session, principal.id, new_hash)
        await revoke_tokens(session, principal.id)
        record(
            session,
            None,
            actor_id=principal.id,
            action="user.password.reset",
            target_kind="user",
            target_id=principal.id,
        )


async def disable_account(storage: Storage, access: Access, actor: Principal, body: AccountDisable) -> None:
    """Disable the caller's own account: authentication fails from now on, every login session and outstanding
    link ends, and accepted work stops at its next authority refresh. Grants and keys stay, so re-enabling
    restores them. The last active administrator of an organization cannot leave it unadministered."""
    require_login_session(actor)
    stored = await _verify_current(storage, actor, body.current_password)
    async with transaction(storage) as session:
        administered = await _administered(session, access, actor.id)
        for organization_id in administered:
            await lock_organization(session, organization_id)
        row = await lock(session, PrincipalRow, actor.id)
        assert row is not None
        # Grants are added only under the principal's lock, so the set is settled now; one added before the
        # lock was taken sits in an organization that is not locked here.
        if set(await _administered(session, access, actor.id)) - set(administered):
            raise conflict("user", actor.id, "concurrent_change")
        await _check_password_unchanged(session, actor.id, stored)
        for organization_id in administered:
            if not await other_administrator(session, access, organization_id, actor.id):
                raise conflict("user", actor.id, "last_organization_admin")
        row.status = "disabled"
        await revoke_tokens(session, row.id)
        record(session, None, actor_id=actor.id, action="user.disable", target_kind="user", target_id=actor.id)


async def set_account_status(storage: Storage, email: str, status: Literal["active", "disabled"]) -> str:
    """The deployment operator's switch for a user account, outside any tenant's authority; returns the user ID.

    Disabling ends every login session and outstanding link, so enabling never revives one issued before; grants
    and keys stay, so enabling restores what disabling suspended.
    """
    async with transaction(storage) as session:
        row = await session.scalar(
            select(PrincipalRow).where(PrincipalRow.kind == "user", PrincipalRow.email == email).with_for_update()
        )
        if row is None:
            raise not_found("user", email)
        if row.status != status:
            row.status = status
            if status == "disabled":
                await revoke_tokens(session, row.id)
            record(
                session,
                None,
                actor_id=None,
                action="user.enable" if status == "active" else "user.disable",
                target_kind="user",
                target_id=row.id,
                details={"authority": "operator"},
            )
        return row.id


async def list_login_sessions(
    storage: Storage, actor: Principal, *, current_session_id: str | None, limit: int, cursor: str | None
) -> LoginSessionPage:
    require_login_session(actor)
    async with short_session(storage) as session:
        rows, next_cursor = await cursors.id_page(
            session,
            select(TokenRow).where(
                TokenRow.principal_id == actor.id,
                TokenRow.kind == "session",
                TokenRow.revoked_at.is_(None),
                TokenRow.expires_at > await now(session),
            ),
            TokenRow.id,
            kind="login_sessions",
            owner=actor.id,
            cursor=cursor,
            limit=limit,
        )
    return LoginSessionPage(
        items=[
            LoginSession(
                id=row.id, created_at=row.created_at, expires_at=row.expires_at, current=row.id == current_session_id
            )
            for row in rows
        ],
        next_cursor=next_cursor,
    )


async def revoke_login_session(storage: Storage, actor: Principal, session_id: str) -> None:
    """End one of the caller's login sessions; logout is this for the current one."""
    require_login_session(actor)
    async with transaction(storage) as session:
        token = await lock(session, TokenRow, session_id)
        if token is None or token.principal_id != actor.id or token.kind != "session":
            raise not_found("login_session", session_id)
        if token.revoked_at is not None:
            return
        token.revoked_at = await now(session)
        record(
            session,
            None,
            actor_id=actor.id,
            action="login_session.revoke",
            target_kind="login_session",
            target_id=token.id,
        )


async def _record_update(session: AsyncSession, row: PrincipalRow, changed: list[str]) -> None:
    """Write and audit a change of the caller's own profile; nothing is recorded when nothing changed."""
    if not changed:
        return
    await session.flush()
    record(
        session,
        None,
        actor_id=row.id,
        action="user.update",
        target_kind="user",
        target_id=row.id,
        details={"fields": [*changed]},
    )


async def _current_profile(
    session: AsyncSession, principal_id: str, if_match: str | None, *, locked: bool = True
) -> PrincipalRow:
    """The caller's profile at the version its ETag names; locked for a change, unless only checked first."""
    if locked:
        row = await lock(session, PrincipalRow, principal_id)
    else:
        row = await session.get(PrincipalRow, principal_id)
    if row is None:
        raise not_found("principal", principal_id)
    require_match(if_match, row.id, row.version)
    return row


async def _administered(session: AsyncSession, access: Access, principal_id: str) -> list[str]:
    """The organizations the principal administers, in lock order."""
    rows = await session.scalars(
        select(GrantRow.organization_id)
        .where(
            GrantRow.principal_id == principal_id,
            GrantRow.workspace_id.is_(None),
            GrantRow.role.in_(access.roles_with("admin")),
        )
        .order_by(GrantRow.organization_id)
    )
    return list(rows)


def _queue_account_mail(session: AsyncSession, keys: KeyRing, settings: Settings, mail: Mail, *, purpose: str) -> None:
    """Account mail belongs to the user, not to any organization they may or may not belong to."""
    queue_mail(session, keys, settings.auth.mail, mail, organization_id=None, workspace_id=None, purpose=purpose)


async def _verify_current(storage: Storage, actor: Principal, password: SecretStr | None) -> str:
    """The stored hash, once the presented current password matches it (verified outside any transaction)."""
    if password is None:
        raise invalid("current_password", "required")
    async with short_session(storage) as session:
        stored = await session.get(PasswordRow, actor.id)
    verified = await verify_password(stored.hash if stored else None, password.get_secret_value())
    if stored is None or not verified:
        raise invalid("current_password", "incorrect")
    return stored.hash


async def _check_password_unchanged(session: AsyncSession, principal_id: str, stored: str | None) -> None:
    current = await lock(session, PasswordRow, principal_id)
    if current is None or current.hash != stored:
        raise invalid("current_password", "incorrect")
