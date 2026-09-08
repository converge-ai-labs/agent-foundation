"""Local password login and database-backed browser session lifecycle."""

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.ids import new_object_id
from a13n_service.storage import short_session
from a13n_service.temporal import utc_now

from ..audit import AuthenticationAuditActor
from ..authentication import AuthenticationError
from ..domain import AuthenticatedActor, AuthorizationError
from ..models import AuthSessionRecord, PasswordCredentialRecord, UserRecord
from ..schemas import AuthSession, User
from ..service_common import audit, identity_transaction, not_found, singleton_organization
from .credentials import require_current_credential
from .passwords import Passwords, new_token, token_hash


@dataclass(frozen=True, slots=True)
class Login:
    user: User
    session: AuthSession
    token: str = field(repr=False)


def create_session(session: AsyncSession, user: UserRecord, *, session_days: int, request_id: str | None) -> Login:
    now = utc_now()
    token = new_token()
    row = AuthSessionRecord(
        id=new_object_id("ase"),
        user_id=user.id,
        token_hash=token_hash(token),
        created_at=now,
        expires_at=now + timedelta(days=session_days),
        revoked_at=None,
    )
    session.add(row)
    audit(
        session,
        actor=AuthenticationAuditActor(user.id, "password", row.id, request_id),
        action="auth_session.create",
        resource_type="auth_session",
        resource_id=row.id,
        organization_id=None,
        now=now,
    )
    return Login(User.model_validate(user), AuthSession.model_validate(row), token)


async def require_user(session: AsyncSession, actor: AuthenticatedActor, *, browser: bool = False) -> UserRecord:
    await require_current_credential(session, actor)
    if actor.principal.principal_type != "user" or (browser and actor.auth_method != "session"):
        raise AuthorizationError("permission_denied")
    user = await session.get(UserRecord, actor.principal.principal_id)
    if user is None or user.status != "active":
        raise AuthorizationError("principal_inactive")
    return user


class SessionService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], passwords: Passwords, *, session_days: int) -> None:
        self._sessions = sessions
        self._passwords = passwords
        self._days = session_days

    async def login(self, email: str, password: str, *, request_id: str | None) -> Login:
        async with short_session(self._sessions) as session:
            row = (
                await session.execute(
                    select(UserRecord.id, PasswordCredentialRecord.password_hash)
                    .join(PasswordCredentialRecord, PasswordCredentialRecord.user_id == UserRecord.id)
                    .where(UserRecord.normalized_email == email.casefold(), UserRecord.status == "active")
                )
            ).one_or_none()
            user_id, verifier = (None, None) if row is None else row
        valid = await self._passwords.verify(verifier, password)
        login = None
        async with identity_transaction(self._sessions, await self.organization_id()) as session:
            if valid:
                user = await session.scalar(select(UserRecord).where(UserRecord.id == user_id).with_for_update())
                current = await session.get(PasswordCredentialRecord, user_id)
                if (
                    user is not None
                    and user.status == "active"
                    and current is not None
                    and current.password_hash == verifier
                ):
                    login = create_session(session, user, session_days=self._days, request_id=request_id)
            if login is None:
                audit(
                    session,
                    actor=AuthenticationAuditActor(None, "password", None, request_id),
                    action="auth.login",
                    resource_type="user",
                    resource_id=None,
                    organization_id=None,
                    outcome="failure",
                )
        if login is None:
            raise AuthenticationError("invalid credentials")
        return login

    async def me(self, actor: AuthenticatedActor) -> User:
        async with short_session(self._sessions) as session:
            return User.model_validate(await require_user(session, actor))

    async def revoke(self, actor: AuthenticatedActor, session_id: str) -> None:
        async with identity_transaction(self._sessions, await self.organization_id()) as session:
            user = await require_user(session, actor, browser=True)
            row = await session.scalar(
                select(AuthSessionRecord)
                .where(
                    AuthSessionRecord.id == session_id,
                    AuthSessionRecord.user_id == user.id,
                )
                .with_for_update()
            )
            if row is None:
                raise not_found()
            if row.revoked_at is None:
                row.revoked_at = utc_now()
                audit(
                    session,
                    actor=actor,
                    action="auth_session.revoke",
                    resource_type="auth_session",
                    resource_id=row.id,
                    organization_id=None,
                )

    async def change_password(self, actor: AuthenticatedActor, current_password: str, password: str) -> None:
        async with short_session(self._sessions) as session:
            user = await require_user(session, actor, browser=True)
            credential = await session.get(PasswordCredentialRecord, user.id)
            old_hash = None if credential is None else credential.password_hash
        if not await self._passwords.verify(old_hash, current_password):
            raise AuthenticationError("invalid credentials")
        new_hash = await self._passwords.hash(password)
        async with identity_transaction(self._sessions, await self.organization_id()) as session:
            user = await require_user(session, actor, browser=True)
            await session.execute(select(UserRecord.id).where(UserRecord.id == user.id).with_for_update())
            credential = await session.get(PasswordCredentialRecord, user.id)
            if credential is None or credential.password_hash != old_hash:
                raise AuthenticationError("credentials changed")
            credential.password_hash = new_hash
            credential.password_changed_at = utc_now()
            await session.execute(
                update(AuthSessionRecord)
                .where(
                    AuthSessionRecord.user_id == user.id,
                    AuthSessionRecord.id != actor.credential_id,
                    AuthSessionRecord.revoked_at.is_(None),
                )
                .values(revoked_at=utc_now())
            )
            audit(
                session,
                actor=actor,
                action="user_credentials.manage",
                resource_type="user",
                resource_id=user.id,
                organization_id=None,
            )

    async def organization_id(self) -> str:
        async with short_session(self._sessions) as session:
            return (await singleton_organization(session)).id
