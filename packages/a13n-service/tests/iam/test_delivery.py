import aiosmtplib
import pytest
from a13n_service.iam.configuration import IdentityConfiguration
from a13n_service.iam.mail import SmtpInvitationMailer


@pytest.mark.anyio
async def test_smtp_failure_does_not_log_message_or_server_reply(monkeypatch, caplog):
    async def fail(message, **kwargs):
        assert kwargs["start_tls"] is True
        assert kwargs["use_tls"] is False
        assert "sensitive-invitation-token" in message.get_content()
        raise aiosmtplib.SMTPException("server echoed sensitive-invitation-token")

    monkeypatch.setattr(aiosmtplib, "send", fail)
    mailer = SmtpInvitationMailer(
        IdentityConfiguration(
            public_origin="https://testserver",
            smtp_host="smtp.example.com",
            smtp_sender="identity@example.com",
        )
    )
    assert await mailer.send("recipient@example.com", "https://testserver/#sensitive-invitation-token") is False
    assert "iam_invitation_delivery_failed" in caplog.text
    assert "sensitive-invitation-token" not in caplog.text
    assert "recipient@example.com" not in caplog.text


@pytest.mark.parametrize(
    "origin",
    ["https://user:pass@example.com", "http://example.com", "https://example.com/path", "https://example.com?secret=x"],
)
def test_public_origin_is_an_exact_safe_origin(origin):
    with pytest.raises(ValueError):
        IdentityConfiguration(public_origin=origin)
