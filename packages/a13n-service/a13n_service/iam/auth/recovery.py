"""Single-use email proofs with short, atomic credential updates."""

from datetime import timedelta
from urllib.parse import urlencode

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.ids import new_object_id
from a13n_service.storage import short_session
from a13n_service.temporal import assume_utc, utc_now

from ..audit import AuthenticationAuditActor
from ..authentication import AuthenticationError
from ..configuration import IdentityConfiguration
from ..domain import AuthenticatedActor
from ..management.mail import SmtpMailer
from ..models import AuthSessionRecord, EmailChangeRecord, PasswordCredentialRecord, PasswordResetRecord, UserRecord
from ..service_common import audit, identity_error, identity_transaction, singleton_organization
from .passwords import Passwords, new_token, token_hash
from .sessions import require_user


class RecoveryService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], configuration: IdentityConfiguration, passwords: Passwords
    ) -> None:
        self._sessions = sessions
        self._configuration = configuration
        self._passwords = passwords
        self._mailer = SmtpMailer(configuration)

    def require_mail(self) -> None:
        if not self._configuration.smtp_host:
            raise identity_error(
                "email_delivery_unavailable", "Email delivery is not configured.", ErrorCategory.unavailable
            )

    async def organization_id(self) -> str:
        async with short_session(self._sessions) as session:
            return (await singleton_organization(session)).id

    async def request_reset(self, email: str) -> None:
        self.require_mail()
        token = new_token()
        destination = None
        async with identity_transaction(self._sessions, await self.organization_id()) as session:
            user = await session.scalar(
                select(UserRecord).where(
                    UserRecord.normalized_email == email.casefold(),
                    UserRecord.status == "active",
                    UserRecord.email_verified_at.is_not(None),
                )
            )
            credential = None if user is None else await session.get(PasswordCredentialRecord, user.id)
            if user is not None and credential is not None:
                now = utc_now()
                await session.execute(
                    update(PasswordResetRecord)
                    .where(PasswordResetRecord.user_id == user.id, PasswordResetRecord.consumed_at.is_(None))
                    .values(consumed_at=now)
                )
                session.add(
                    PasswordResetRecord(
                        id=new_object_id("prt"),
                        user_id=user.id,
                        token_hash=token_hash(token),
                        password_hash=credential.password_hash,
                        expires_at=now + timedelta(minutes=30),
                        consumed_at=None,
                        created_at=now,
                    )
                )
                destination = user.email
        if destination is not None:
            # Delivery outcome is intentionally indistinguishable from an unknown address.
            await self._send(destination, "Reset your a13n password", "/reset-password", token)

    async def complete_reset(self, token: str, password: str, request_id: str | None) -> None:
        verifier = token_hash(token)
        async with short_session(self._sessions) as session:
            row = await session.scalar(select(PasswordResetRecord).where(PasswordResetRecord.token_hash == verifier))
            self._validate_token(row)
        password_hash = await self._passwords.hash(password)
        async with identity_transaction(self._sessions, await self.organization_id()) as session:
            row = await session.scalar(select(PasswordResetRecord).where(PasswordResetRecord.token_hash == verifier))
            self._validate_token(row)
            assert row is not None
            user = await session.get(UserRecord, row.user_id)
            credential = await session.get(PasswordCredentialRecord, row.user_id)
            if (
                user is None
                or user.status != "active"
                or credential is None
                or credential.password_hash != row.password_hash
            ):
                raise self._invalid_token()
            now = utc_now()
            credential.password_hash, credential.password_changed_at = password_hash, now
            await session.execute(
                update(PasswordResetRecord)
                .where(PasswordResetRecord.user_id == user.id, PasswordResetRecord.consumed_at.is_(None))
                .values(consumed_at=now)
            )
            await session.execute(
                update(AuthSessionRecord)
                .where(AuthSessionRecord.user_id == user.id, AuthSessionRecord.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            audit(
                session,
                actor=AuthenticationAuditActor(user.id, "password", None, request_id),
                action="user_credentials.reset",
                resource_type="user",
                resource_id=user.id,
                organization_id=None,
            )

    async def request_email_change(self, actor: AuthenticatedActor, email: str, password: str) -> None:
        self.require_mail()
        async with short_session(self._sessions) as session:
            user = await require_user(session, actor, browser=True)
            credential = await session.get(PasswordCredentialRecord, user.id)
            old_hash = None if credential is None else credential.password_hash
        if not await self._passwords.verify(old_hash, password):
            raise AuthenticationError("invalid credentials")
        token = new_token()
        async with identity_transaction(self._sessions, await self.organization_id()) as session:
            user = await require_user(session, actor, browser=True)
            credential = await session.get(PasswordCredentialRecord, user.id)
            if credential is None or credential.password_hash != old_hash:
                raise AuthenticationError("credentials changed")
            if user.normalized_email == email.casefold():
                raise identity_error("email_unchanged", "This is already your email address.")
            now = utc_now()
            await session.execute(
                update(EmailChangeRecord)
                .where(EmailChangeRecord.user_id == user.id, EmailChangeRecord.consumed_at.is_(None))
                .values(consumed_at=now)
            )
            session.add(
                EmailChangeRecord(
                    id=new_object_id("ect"),
                    user_id=user.id,
                    token_hash=token_hash(token),
                    previous_email=user.normalized_email,
                    new_email=email,
                    new_normalized_email=email.casefold(),
                    expires_at=now + timedelta(minutes=30),
                    consumed_at=None,
                    created_at=now,
                )
            )
        if not await self._send(email, "Confirm your a13n email address", "/confirm-email", token):
            raise identity_error(
                "email_delivery_failed", "The verification email could not be delivered.", ErrorCategory.unavailable
            )

    async def complete_email_change(self, actor: AuthenticatedActor, token: str) -> None:
        async with identity_transaction(self._sessions, await self.organization_id()) as session:
            user = await require_user(session, actor, browser=True)
            row = await session.scalar(
                select(EmailChangeRecord).where(
                    EmailChangeRecord.user_id == user.id, EmailChangeRecord.token_hash == token_hash(token)
                )
            )
            self._validate_token(row)
            assert row is not None
            if user.normalized_email != row.previous_email:
                raise self._invalid_token()
            if await session.scalar(
                select(UserRecord.id).where(UserRecord.normalized_email == row.new_normalized_email)
            ):
                raise identity_error("email_unavailable", "This email address cannot be used.")
            now = utc_now()
            user.email, user.normalized_email = row.new_email, row.new_normalized_email
            user.email_verified_at = user.updated_at = now
            row.consumed_at = now
            await session.execute(
                update(PasswordResetRecord)
                .where(PasswordResetRecord.user_id == user.id, PasswordResetRecord.consumed_at.is_(None))
                .values(consumed_at=now)
            )
            audit(
                session,
                actor=actor,
                action="user_profile.email_change",
                resource_type="user",
                resource_id=user.id,
                organization_id=None,
            )

    async def _send(self, email: str, subject: str, path: str, token: str) -> bool:
        # Fragment tokens stay out of ingress access logs and referrer headers.
        url = f"{self._configuration.public_origin}{path}#{urlencode({'token': token})}"
        return await self._mailer.send_message(
            email, subject, f"Use this single-use link within 30 minutes:\n\n{url}\n"
        )

    @staticmethod
    def _invalid_token():
        return identity_error(
            "invalid_identity_token", "The link is invalid or has expired.", ErrorCategory.invalid_request
        )

    @classmethod
    def _validate_token(cls, row: PasswordResetRecord | EmailChangeRecord | None) -> None:
        if row is None or row.consumed_at is not None or assume_utc(row.expires_at) <= utc_now():
            raise cls._invalid_token()
