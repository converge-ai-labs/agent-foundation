"""Validated operator configuration for local OSS identity and link delivery."""

from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, model_validator


class IdentityConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    public_origin: str = Field(min_length=1, max_length=2048)
    initial_admin_email: EmailStr | None = None
    session_days: int = Field(default=7, ge=1, le=90)
    invitation_days: int = Field(default=7, ge=1, le=30)
    smtp_host: str | None = Field(default=None, max_length=253)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, max_length=320, repr=False)
    smtp_password: SecretStr | None = None
    smtp_sender: EmailStr | None = None
    smtp_tls: Literal["starttls", "tls"] = "starttls"

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        url = urlsplit(self.public_origin)
        _ = url.port  # Validate numeric range before accepting a link origin.
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.path
            or url.query
            or url.fragment
            or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})
        ):
            raise ValueError("IAM public origin must be an HTTPS origin (HTTP is allowed only on loopback)")
        if self.smtp_host is None:
            if any(value is not None for value in (self.smtp_username, self.smtp_password, self.smtp_sender)):
                raise ValueError("SMTP credentials and sender require an SMTP host")
        elif self.smtp_sender is None:
            raise ValueError("SMTP requires a sender address")
        if (self.smtp_username is None) != (self.smtp_password is None):
            raise ValueError("SMTP username and password must be configured together")
        return self
