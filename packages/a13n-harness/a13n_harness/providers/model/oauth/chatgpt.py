"""Sign in with ChatGPT public-client registration and verified OAuth credentials.

Protocol values are independent of Models and Host storage. Hosts persist the
pending attempt, consume it once, and publish only a completely validated result.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from urllib.parse import parse_qs, quote_plus, urlencode, urlsplit

import httpx2
import jwt
from pydantic_ai.exceptions import UserError

from .flow import expiry, json_object, nonempty, oauth_request, positive_number, required_string
from .models import CredentialRefreshError, RefreshNotDispatched

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
DYNAMIC_CLIENT_ID = "dynamic_agent_client"
SCOPES = ("openid", "profile", "email", "offline_access", "resource.invoke", "chatgpt.tokens.use.direct")
_REQUIRED_SCOPES = frozenset({"resource.invoke", "chatgpt.tokens.use.direct"})


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenAIChatGPTCredentials:
    """One verified registration and complete renewable token set."""

    subject: str
    client_id: str
    ext_agent_host_id: str
    expires_at: datetime
    scopes: tuple[str, ...]
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    id_token: str = field(repr=False)
    issuer: str = ISSUER
    email: str | None = None
    earliest_refresh_at: datetime | None = None
    token_endpoint_auth_method: Literal["none", "client_secret_basic"] = "none"
    client_secret: str | None = field(default=None, repr=False)

    @property
    def provider(self) -> str:
        return "openai-chatgpt"

    @property
    def account_id(self) -> str:
        return self.subject


type OpenAIChatGPTRefresh = Callable[[OpenAIChatGPTCredentials], Awaitable[OpenAIChatGPTCredentials]]


class OpenAIChatGPTCredentialSource(Protocol):
    """Host-owned acquisition, grant exclusion, and publication before use."""

    async def load(self) -> OpenAIChatGPTCredentials: ...

    async def rotate(
        self, expected: OpenAIChatGPTCredentials, exchange: OpenAIChatGPTRefresh
    ) -> OpenAIChatGPTCredentials: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class ChatGPTAuthorization:
    """A bounded pending attempt; protected storage, never browser storage."""

    ext_agent_host_id: str
    agent_name: str
    redirect_uri: str
    expires_at: datetime
    state: str = field(repr=False)
    nonce: str = field(repr=False)
    code_verifier: str = field(repr=False)
    client_id: str = DYNAMIC_CLIENT_ID
    # Explicit Host configuration, not an issued ID from OSS dynamic registration.
    preconfigured_client: bool = False
    token_endpoint_auth_method: Literal["none", "client_secret_basic"] = "none"
    client_secret: str | None = field(default=None, repr=False)
    subject: str | None = None
    id_token_hint: str | None = field(default=None, repr=False)
    login_hint: str | None = None


@dataclass(frozen=True, slots=True)
class ChatGPTCallback:
    code: str = field(repr=False)
    client_id: str


class OpenAIChatGPTOAuthFlow:
    """One PKCE attempt. Pasted and automatic callbacks use `validate_callback`."""

    def __init__(self, authorization: ChatGPTAuthorization, *, http_client: httpx2.AsyncClient | None = None):
        validate_chatgpt_redirect_uri(
            authorization.redirect_uri, preconfigured_client=authorization.preconfigured_client
        )
        if authorization.preconfigured_client and authorization.client_id == DYNAMIC_CLIENT_ID:
            raise UserError("A preconfigured ChatGPT client must have its own client ID.")
        validate_client_authentication(
            authorization.client_id, authorization.token_endpoint_auth_method, authorization.client_secret
        )
        self.authorization = authorization
        self._http_client = http_client
        self._consumed = False

    @classmethod
    def start(
        cls,
        *,
        ext_agent_host_id: str,
        agent_name: str,
        redirect_uri: str,
        client_id: str | None = None,
        token_endpoint_auth_method: Literal["none", "client_secret_basic"] = "none",
        client_secret: str | None = None,
        credentials: OpenAIChatGPTCredentials | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> OpenAIChatGPTOAuthFlow:
        if client_id is not None:
            client_id = nonempty(client_id, "client_id")
            if client_id == DYNAMIC_CLIENT_ID:
                raise UserError("A preconfigured ChatGPT client must have its own client ID.")
        if credentials is not None:
            if credentials.ext_agent_host_id != ext_agent_host_id:
                raise UserError("The ChatGPT registration belongs to another host.")
            if client_id is not None and credentials.client_id != client_id:
                raise UserError("The ChatGPT registration belongs to another client.")
        return cls(
            ChatGPTAuthorization(
                ext_agent_host_id=nonempty(ext_agent_host_id, "ext_agent_host_id"),
                agent_name=nonempty(agent_name, "agent_name"),
                redirect_uri=redirect_uri,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
                state=secrets.token_urlsafe(32),
                nonce=secrets.token_urlsafe(32),
                code_verifier=secrets.token_urlsafe(64),
                client_id=client_id or (credentials.client_id if credentials is not None else DYNAMIC_CLIENT_ID),
                preconfigured_client=client_id is not None,
                token_endpoint_auth_method=token_endpoint_auth_method,
                client_secret=client_secret,
                subject=credentials.subject if credentials is not None else None,
                id_token_hint=credentials.id_token if credentials is not None else None,
                login_hint=credentials.email if credentials is not None else None,
            ),
            http_client=http_client,
        )

    def authorization_url(self) -> str:
        pending = self.authorization
        challenge = base64.urlsafe_b64encode(hashlib.sha256(pending.code_verifier.encode()).digest()).rstrip(b"=")
        params = {
            "client_id": pending.client_id,
            "ext_agent_host_id": pending.ext_agent_host_id,
            "response_type": "code",
            "redirect_uri": pending.redirect_uri,
            "scope": " ".join(SCOPES),
            "resource": RESOURCE,
            "state": pending.state,
            "nonce": pending.nonce,
            "code_challenge": challenge.decode(),
            "code_challenge_method": "S256",
        }
        if pending.client_id == DYNAMIC_CLIENT_ID:
            params["agent_name_hint"] = pending.agent_name
        else:
            if pending.id_token_hint:
                params["id_token_hint"] = pending.id_token_hint
            if pending.login_hint:
                params["login_hint"] = pending.login_hint
        return f"{ISSUER}/api/accounts/authorize?{urlencode(params)}"

    def validate_callback(self, callback_url: str) -> ChatGPTCallback:
        """Parse data only. Never request a user-supplied callback URL."""
        pending = self.authorization
        try:
            parsed = urlsplit(callback_url.strip())
            target = urlsplit(pending.redirect_uri)
            params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
            matches = (
                (parsed.scheme, parsed.netloc, parsed.path) == (target.scheme, target.netloc, target.path)
                and not parsed.fragment
                and all(len(values) == 1 for values in params.values())
                and secrets.compare_digest(params.get("state", [""])[0], pending.state)
            )
        except (ValueError, TypeError):
            matches = False
            params = {}
        if not matches:
            raise UserError("The ChatGPT callback URL does not match this authorization attempt.")
        if pending.expires_at.tzinfo is None or datetime.now(UTC) >= pending.expires_at:
            raise UserError("ChatGPT authorization expired. Start a new sign-in.")
        if "error" in params:
            raise UserError("ChatGPT authorization was not granted. Start a new sign-in.")
        code = params.get("code", [""])[0]
        issued = params.get("client_id", [""])[0]
        if pending.client_id == DYNAMIC_CLIENT_ID:
            if not issued or issued == DYNAMIC_CLIENT_ID:
                raise UserError("ChatGPT registration did not return an issued client ID.")
        else:
            if issued and issued != pending.client_id:
                raise UserError("The ChatGPT callback changed the selected registration.")
            issued = pending.client_id
        if not code:
            raise UserError("The ChatGPT callback URL has no authorization code.")
        return ChatGPTCallback(code=code, client_id=issued)

    async def exchange_callback(self, callback_url: str) -> OpenAIChatGPTCredentials:
        callback = self.validate_callback(callback_url)
        if self._consumed:
            raise UserError("This ChatGPT authorization attempt was already consumed. Start a new sign-in.")
        self._consumed = True
        discovery = await _discovery(self._http_client)
        document = await _token(
            {
                "grant_type": "authorization_code",
                "code": callback.code,
                "client_id": callback.client_id,
                "code_verifier": self.authorization.code_verifier,
                "redirect_uri": self.authorization.redirect_uri,
                "resource": RESOURCE,
            },
            http_client=self._http_client,
            client_secret=self.authorization.client_secret,
        )
        return await _credentials(
            document,
            client_id=callback.client_id,
            host_id=self.authorization.ext_agent_host_id,
            discovery=discovery,
            nonce=self.authorization.nonce,
            subject=self.authorization.subject,
            http_client=self._http_client,
            token_endpoint_auth_method=self.authorization.token_endpoint_auth_method,
            client_secret=self.authorization.client_secret,
        )


def validate_chatgpt_redirect_uri(value: str, *, preconfigured_client: bool = False) -> None:
    """Validate syntax only; the Host must use an exact URI registered with OpenAI.

    An OSS-issued client ID never opts into website redirects. Only an explicitly
    preconfigured client can use HTTPS or its own loopback callback path.
    """
    try:
        parsed = urlsplit(value)
        loopback = parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port is not None
        valid = (
            (loopback or (preconfigured_client and parsed.scheme == "https" and bool(parsed.hostname)))
            and (parsed.port is None or 1 <= parsed.port <= 65535)
            and (parsed.path.startswith("/") if preconfigured_client else parsed.path == "/auth/callback")
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and "?" not in value
            and "#" not in value
            and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
        )
    except ValueError:
        valid = False
    if not valid:
        message = (
            "A preconfigured ChatGPT client requires an absolute HTTPS or HTTP 127.0.0.1 callback URI without "
            "userinfo, query or fragment."
            if preconfigured_client
            else "ChatGPT OSS registration requires http://127.0.0.1:<port>/auth/callback as its callback URI."
        )
        raise UserError(message)


async def _discovery(http_client: httpx2.AsyncClient | None) -> dict[str, object]:
    response = await oauth_request("GET", f"{ISSUER}/.well-known/openid-configuration", http_client=http_client)
    if response.status_code != 200:
        raise CredentialRefreshError("openai-chatgpt", "OpenAI discovery failed.")
    document = json_object(response, provider="openai-chatgpt")
    if document.get("issuer") != ISSUER:
        raise CredentialRefreshError("openai-chatgpt", "OpenAI discovery returned an unexpected issuer.")
    jwks_uri = required_string(document, "jwks_uri", "openai-chatgpt")
    parsed = urlsplit(jwks_uri)
    if parsed.scheme != "https" or parsed.netloc != "auth.openai.com" or parsed.fragment:
        raise CredentialRefreshError("openai-chatgpt", "OpenAI discovery returned an invalid JWKS endpoint.")
    return document


async def _identity(
    token: str,
    *,
    client_id: str,
    nonce: str | None,
    discovery: dict[str, object],
    http_client: httpx2.AsyncClient | None,
) -> dict[str, object]:
    response = await oauth_request("GET", str(discovery["jwks_uri"]), http_client=http_client)
    if response.status_code != 200:
        raise CredentialRefreshError("openai-chatgpt", "OpenAI signing keys could not be loaded.")
    try:
        keys = jwt.PyJWKSet.from_dict(response.json())
        header = jwt.get_unverified_header(token)
        # Restrict to asymmetric algorithms, independently of the untrusted JWT header.
        advertised_algorithms = discovery.get("id_token_signing_alg_values_supported", ["RS256"])
        if not isinstance(advertised_algorithms, list):
            raise ValueError("invalid signing algorithms")
        algorithms = [
            value for value in advertised_algorithms if value in ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")
        ]
        key = keys[header["kid"]]
        claims = jwt.decode(
            token,
            key.key,
            algorithms=algorithms,
            audience=client_id,
            issuer=ISSUER,
            leeway=5,
            options={"require": ["sub", "exp", "iat", "iss", "aud"]},
        )
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise ValueError("missing subject")
        if nonce is not None and claims.get("nonce") != nonce:
            raise ValueError("nonce mismatch")
        if claims.get("azp", client_id) != client_id:
            raise ValueError("authorized party mismatch")
        return claims
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise CredentialRefreshError("openai-chatgpt", "The ChatGPT ID token could not be verified.") from None


def validate_client_authentication(client_id: str | None, method: str, client_secret: str | None) -> None:
    if (
        method not in ("none", "client_secret_basic")
        or (method == "client_secret_basic" and (not client_id or client_id == DYNAMIC_CLIENT_ID or not client_secret))
        or (method == "none" and client_secret is not None)
    ):
        raise UserError("Use the registered token endpoint authentication method and client secret.")


def _client_headers(client_id: str, client_secret: str | None) -> dict[str, str]:
    if client_secret is None:
        return {}
    encoded = base64.b64encode(f"{quote_plus(client_id)}:{quote_plus(client_secret)}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


async def _token(
    form: dict[str, str], *, http_client: httpx2.AsyncClient | None, client_secret: str | None = None
) -> dict[str, object]:
    response = await oauth_request(
        "POST",
        f"{ISSUER}/api/accounts/oauth/token",
        data=form,
        http_client=http_client,
        headers=_client_headers(form["client_id"], client_secret),
    )
    if response.status_code != 200:
        raise CredentialRefreshError("openai-chatgpt", f"ChatGPT token exchange failed (HTTP {response.status_code}).")
    return json_object(response, provider="openai-chatgpt")


async def _credentials(
    document: dict[str, object],
    *,
    client_id: str,
    host_id: str,
    discovery: dict[str, object],
    nonce: str | None,
    subject: str | None,
    http_client: httpx2.AsyncClient | None,
    token_endpoint_auth_method: Literal["none", "client_secret_basic"] = "none",
    client_secret: str | None = None,
) -> OpenAIChatGPTCredentials:
    provider = "openai-chatgpt"
    id_token = required_string(document, "id_token", provider)
    claims = await _identity(id_token, client_id=client_id, nonce=nonce, discovery=discovery, http_client=http_client)
    if subject is not None and claims["sub"] != subject:
        raise CredentialRefreshError(provider, "ChatGPT sign-in returned a different account.")
    granted = tuple(required_string(document, "scope", provider).split())
    if not _REQUIRED_SCOPES.issubset(granted):
        raise CredentialRefreshError(provider, "ChatGPT plan usage was not granted. Authorize plan usage to continue.")
    if required_string(document, "token_type", provider).lower() != "bearer":
        raise CredentialRefreshError(provider, "ChatGPT returned an unsupported token type.")
    now = datetime.now(UTC)
    earliest = document.get("earliest_refresh_at")
    earliest_at = (
        datetime.fromtimestamp(positive_number(earliest, "earliest_refresh_at", provider=provider), UTC)
        if earliest is not None
        else None
    )
    email = claims.get("email")
    return OpenAIChatGPTCredentials(
        subject=str(claims["sub"]),
        client_id=client_id,
        ext_agent_host_id=host_id,
        access_token=required_string(document, "access_token", provider),
        refresh_token=required_string(document, "refresh_token", provider),
        id_token=id_token,
        scopes=granted,
        expires_at=expiry(
            now, positive_number(document.get("expires_in"), "expires_in", provider=provider), provider=provider
        ),
        email=email if isinstance(email, str) else None,
        earliest_refresh_at=earliest_at,
        token_endpoint_auth_method=token_endpoint_auth_method,
        client_secret=client_secret,
    )


async def refresh_chatgpt_credentials(
    credentials: OpenAIChatGPTCredentials, *, http_client: httpx2.AsyncClient | None = None
) -> OpenAIChatGPTCredentials:
    validate_client_authentication(
        credentials.client_id, credentials.token_endpoint_auth_method, credentials.client_secret
    )
    try:
        discovery = await _discovery(http_client)
    except Exception:
        raise RefreshNotDispatched("openai-chatgpt", "OpenAI discovery failed before token refresh.") from None
    document = await _token(
        {
            "grant_type": "refresh_token",
            "client_id": credentials.client_id,
            "refresh_token": credentials.refresh_token,
            "resource": RESOURCE,
        },
        http_client=http_client,
        client_secret=credentials.client_secret,
    )
    return await _credentials(
        document,
        client_id=credentials.client_id,
        host_id=credentials.ext_agent_host_id,
        discovery=discovery,
        nonce=None,
        subject=credentials.subject,
        http_client=http_client,
        token_endpoint_auth_method=credentials.token_endpoint_auth_method,
        client_secret=credentials.client_secret,
    )


async def revoke_chatgpt_credentials(
    credentials: OpenAIChatGPTCredentials, *, http_client: httpx2.AsyncClient | None = None
) -> None:
    validate_client_authentication(
        credentials.client_id, credentials.token_endpoint_auth_method, credentials.client_secret
    )
    discovery = await _discovery(http_client)
    endpoint = required_string(discovery, "revocation_endpoint", "openai-chatgpt")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or parsed.netloc != "auth.openai.com":
        raise CredentialRefreshError("openai-chatgpt", "OpenAI returned an invalid revocation endpoint.")
    response = await oauth_request(
        "POST",
        endpoint,
        headers=_client_headers(credentials.client_id, credentials.client_secret),
        data={
            "token": credentials.refresh_token,
            "token_type_hint": "refresh_token",
            "client_id": credentials.client_id,
        },
        http_client=http_client,
    )
    if response.status_code != 200:
        raise CredentialRefreshError("openai-chatgpt", "ChatGPT remote revocation was not confirmed.")
