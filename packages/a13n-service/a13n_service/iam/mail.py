"""Bounded SMTP delivery after invitation persistence has committed."""

import asyncio
import logging
from email.message import EmailMessage
from typing import Protocol

import aiosmtplib

from .configuration import IdentityConfiguration

logger = logging.getLogger("a13n_service.iam.mail")


class InvitationMailer(Protocol):
    async def send(self, email: str, invitation_url: str) -> bool: ...


class SmtpInvitationMailer:
    def __init__(self, configuration: IdentityConfiguration) -> None:
        self._configuration = configuration

    async def send(self, email: str, invitation_url: str) -> bool:
        configuration = self._configuration
        message = EmailMessage()
        message["From"] = configuration.smtp_sender
        message["To"] = email
        message["Subject"] = "Your a13n Service invitation"
        message.set_content(
            f"Complete your a13n Service registration using this single-use link:\n\n{invitation_url}\n"
        )
        try:
            async with asyncio.timeout(30):
                await aiosmtplib.send(
                    message,
                    hostname=configuration.smtp_host,
                    port=configuration.smtp_port,
                    username=configuration.smtp_username,
                    password=None
                    if configuration.smtp_password is None
                    else configuration.smtp_password.get_secret_value(),
                    use_tls=configuration.smtp_tls == "tls",
                    start_tls=configuration.smtp_tls == "starttls",
                    timeout=15,
                )
        except (aiosmtplib.SMTPException, OSError, TimeoutError):
            # SMTP exceptions can contain recipients, server replies, or message data.
            logger.warning("iam_invitation_delivery_failed")
            return False
        return True
