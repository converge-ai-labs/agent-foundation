"""The default OSS identity composition, shared by HTTP and local initialization recovery."""

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .api_keys import ApiKeyService
from .collections import IdentityCollections
from .configuration import IdentityConfiguration
from .http_auth import DatabaseAuthenticator
from .invitations import InvitationService
from .mail import SmtpInvitationMailer
from .membership import MembershipService
from .passwords import Passwords
from .service_accounts import ServiceAccountService
from .sessions import SessionService

logger = logging.getLogger("a13n_service.iam.runtime")


@dataclass(frozen=True, slots=True)
class IdentityRuntime:
    configuration: IdentityConfiguration
    authenticator: DatabaseAuthenticator
    sessions: SessionService
    invitations: InvitationService
    keys: ApiKeyService
    accounts: ServiceAccountService
    membership: MembershipService
    collections: IdentityCollections


async def build_identity_runtime(
    sessions: async_sessionmaker[AsyncSession],
    configuration: IdentityConfiguration,
) -> IdentityRuntime:
    passwords = Passwords()
    await passwords.initialize()
    invitations = InvitationService(
        sessions,
        configuration,
        passwords,
        SmtpInvitationMailer(configuration) if configuration.smtp_host else None,
    )
    return IdentityRuntime(
        configuration=configuration,
        authenticator=DatabaseAuthenticator(sessions, configuration),
        sessions=SessionService(sessions, passwords, session_days=configuration.session_days),
        invitations=invitations,
        keys=ApiKeyService(sessions),
        accounts=ServiceAccountService(sessions),
        membership=MembershipService(sessions),
        collections=IdentityCollections(sessions),
    )


async def initialize_identity(runtime: IdentityRuntime) -> None:
    issued = await runtime.invitations.initialize()
    if issued is None:
        return
    delivery = await runtime.invitations.deliver(issued)
    if delivery.delivery == "manual":
        # This is the explicitly protected bootstrap channel. No other IAM link,
        # token, password, or credential material is emitted to application logs.
        logger.warning("Administrator initialization link (single use): %s", delivery.invitation_url)
    elif delivery.delivery == "failed":
        logger.warning(
            "Administrator invitation was saved but delivery failed; use 'a13n-service iam reissue-bootstrap'."
        )
