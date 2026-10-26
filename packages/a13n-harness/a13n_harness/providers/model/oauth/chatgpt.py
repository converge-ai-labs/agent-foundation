"""Host persistence and callback adapters for Pydantic AI's ChatGPT OAuth flow.

Pydantic AI owns registration, PKCE, token exchange, validation and refresh.
Hosts own pending-attempt persistence, consumption and callback URL admission.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import parse_qs, urlsplit

import jwt
from pydantic import BeforeValidator, ConfigDict
from pydantic_ai.exceptions import UserError
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentials, OpenAIChatGPTOAuthFlow


def _stored_credentials(value: Any) -> Any:
    """Read the former public-client token shape without retaining protocol metadata."""
    if isinstance(value, dict):
        if value.get("token_endpoint_auth_method", "none") != "none":
            raise ValueError("Confidential ChatGPT clients are no longer supported; sign in with a public client")
        if "scopes" in value and "scope" not in value:
            value = {**value, "scope": " ".join(value["scopes"])}
        return {
            key: value[key]
            for key in ("client_id", "access_token", "refresh_token", "id_token", "scope")
            if key in value
        }
    return value


type StoredChatGPTCredentials = Annotated[OpenAIChatGPTCredentials, BeforeValidator(_stored_credentials)]


def credential_metadata(credentials: OpenAIChatGPTCredentials) -> tuple[str | None, str | None, datetime | None]:
    """Display hints only; authentication and token validation belong to Pydantic AI."""

    def claims(token: str | None) -> dict[str, Any]:
        try:
            return jwt.decode(token or "", options={"verify_signature": False})
        except jwt.PyJWTError:
            return {}

    identity = claims(credentials.id_token)
    access = claims(credentials.access_token)
    subject, email = identity.get("sub"), identity.get("email")
    expires_at = None
    if isinstance(access.get("exp"), (int, float)):
        try:
            expires_at = datetime.fromtimestamp(access["exp"], UTC)
        except (ValueError, OverflowError, OSError):
            pass
    return subject if isinstance(subject, str) else None, email if isinstance(email, str) else None, expires_at


@dataclass(frozen=True, slots=True, kw_only=True)
class ChatGPTAuthorization:
    """Serializable public flow attributes plus a Host-owned ten-minute deadline."""

    # Pending public-client attempts from earlier releases carry retired hints.
    __pydantic_config__ = ConfigDict(extra="ignore")

    ext_agent_host_id: str
    agent_name: str
    redirect_uri: str
    expires_at: datetime
    state: str = field(repr=False)
    nonce: str = field(repr=False)
    code_verifier: str = field(repr=False)
    client_id: str | None = None
    id_token_hint: str | None = field(default=None, repr=False)
    request_consent: bool = False

    @classmethod
    def start(
        cls,
        *,
        host_id: str,
        redirect_uri: str,
        client_id: str | None = None,
        credentials: OpenAIChatGPTCredentials | None = None,
    ) -> ChatGPTAuthorization:
        flow = OpenAIChatGPTOAuthFlow(
            app_name="Agent Foundation OSS",
            host_id=host_id,
            redirect_uri=redirect_uri,
            client_id=client_id or (credentials.client_id if credentials else None),
            id_token_hint=credentials.id_token if credentials else None,
        )
        return cls(
            ext_agent_host_id=host_id,
            agent_name=flow.app_name,
            redirect_uri=flow.redirect_uri,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            state=flow.state,
            nonce=flow.nonce,
            code_verifier=flow.code_verifier,
            client_id=flow.client_id,
            id_token_hint=flow.id_token_hint,
            request_consent=credentials is not None and not credentials.plan_usage_granted,
        )

    def flow(self) -> OpenAIChatGPTOAuthFlow:
        flow = OpenAIChatGPTOAuthFlow(
            app_name=self.agent_name,
            host_id=self.ext_agent_host_id,
            redirect_uri=self.redirect_uri,
            state=self.state,
            client_id=None if self.client_id == "dynamic_agent_client" else self.client_id,
            id_token_hint=self.id_token_hint,
        )
        flow.nonce, flow.code_verifier = self.nonce, self.code_verifier
        return flow

    def authorization_url(self) -> str:
        return self.flow().authorization_url(extra_params={"prompt": "consent"} if self.request_consent else None)

    def validate_callback(self, callback_url: str) -> tuple[str, str | None]:
        """Validate browser input as data; never fetch a pasted URL."""
        try:
            parsed, target = urlsplit(callback_url.strip()), urlsplit(self.redirect_uri)
            params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
            matches = (
                (parsed.scheme, parsed.netloc, parsed.path) == (target.scheme, target.netloc, target.path)
                and not parsed.fragment
                and all(len(values) == 1 for values in params.values())
                and secrets.compare_digest(params.get("state", [""])[0], self.state)
            )
        except (ValueError, TypeError):
            matches, params = False, {}
        if not matches:
            raise UserError("The ChatGPT callback URL does not match this authorization attempt.")
        if self.expires_at.tzinfo is None or datetime.now(UTC) >= self.expires_at:
            raise UserError("ChatGPT authorization expired. Start a new sign-in.")
        if "error" in params or not params.get("code", [""])[0]:
            raise UserError("ChatGPT authorization was not granted. Start a new sign-in.")
        return params["code"][0], params.get("client_id", [None])[0]

    async def exchange_callback(self, callback_url: str) -> OpenAIChatGPTCredentials:
        code, client_id = self.validate_callback(callback_url)
        return await self.flow().exchange_code(code, client_id=client_id)
