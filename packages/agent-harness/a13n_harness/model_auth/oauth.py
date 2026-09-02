"""Provider-compatible OAuth exchange and refresh primitives."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit

import anyio.to_thread
import httpx2
from pydantic_ai.exceptions import UserError

from .models import CodexCredentials, CredentialRefreshError, GrokCredentials

_CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
_CODEX_SCOPE = "openid profile email offline_access"
_TIMEOUT = httpx2.Timeout(timeout=30, connect=5)


def _no_auth(request: httpx2.Request) -> httpx2.Request:
    return request


class OAuthFlow[CredentialsT](ABC):
    """Authorization-code plus PKCE context with caller-owned presentation and storage."""

    def __init__(self, *, redirect_uri: str, state: str | None = None) -> None:
        self.redirect_uri = redirect_uri
        self.state = state or secrets.token_urlsafe(16)
        self.code_verifier = secrets.token_urlsafe(32)

    @property
    def code_challenge(self) -> str:
        digest = hashlib.sha256(self.code_verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def _merge_extra_params(
        self,
        params: dict[str, str],
        extra_params: Mapping[str, str] | None,
    ) -> dict[str, str]:
        if extra_params:
            overridden = {"client_id", "redirect_uri"} & extra_params.keys()
            if overridden:
                raise UserError("extra_params cannot override client_id or redirect_uri")
            params.update(extra_params)
        return params

    @abstractmethod
    def authorization_url(
        self,
        *,
        scope: str | None = None,
        extra_params: Mapping[str, str] | None = None,
    ) -> str: ...

    @abstractmethod
    async def exchange_code(self, code: str) -> CredentialsT: ...

    async def exchange_code_from_callback(self) -> CredentialsT:
        parsed = urlparse(self.redirect_uri)
        address = (parsed.hostname or "localhost", parsed.port or 80)
        callback_path = parsed.path
        expected_state = self.state
        result: dict[str, str] = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                try:
                    url = urlparse(self.path)
                except ValueError:
                    self.send_error(400, "Malformed request")
                    return
                params = {name: values[0] for name, values in parse_qs(url.query).items()}
                if url.path == callback_path and params.get("state") == expected_state:
                    if code := params.get("code"):
                        result["code"] = code
                    else:
                        result["error"] = params.get("error", "unknown")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"You can close this tab.")

            def log_message(self, format: str, *args: Any) -> None:
                del format, args

        cancelled = threading.Event()

        def serve() -> None:
            with HTTPServer(address, CallbackHandler) as server:
                server.timeout = 0.5
                while not result and not cancelled.is_set():
                    server.handle_request()

        try:
            await anyio.to_thread.run_sync(serve, abandon_on_cancel=True)
        finally:
            cancelled.set()
        if error := result.get("error"):
            raise UserError(f"Authorization failed: {error}")
        return await self.exchange_code(result["code"])


class CodexOAuthFlow(OAuthFlow[CodexCredentials]):
    """OpenAI public-client Codex authorization-code flow."""

    def __init__(self, *, redirect_uri: str = _CODEX_REDIRECT_URI, state: str | None = None) -> None:
        super().__init__(redirect_uri=redirect_uri, state=state)

    def authorization_url(
        self,
        *,
        scope: str | None = None,
        extra_params: Mapping[str, str] | None = None,
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": _CODEX_CLIENT_ID,
            "redirect_uri": self.redirect_uri,
            "scope": scope if scope is not None else _CODEX_SCOPE,
            "state": self.state,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
        }
        return f"{_CODEX_AUTHORIZE_URL}?{urlencode(self._merge_extra_params(params, extra_params))}"

    async def exchange_code(self, code: str) -> CodexCredentials:
        document = await _post_token(
            _CODEX_TOKEN_URL,
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": self.code_verifier,
                "redirect_uri": self.redirect_uri,
                "client_id": _CODEX_CLIENT_ID,
            },
        )
        return _codex_credentials(document)


async def refresh_codex_credentials(
    credentials: CodexCredentials,
    *,
    http_client: httpx2.AsyncClient | None = None,
) -> CodexCredentials:
    document = await _post_token(
        _CODEX_TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "refresh_token": credentials.refresh_token,
            "client_id": _CODEX_CLIENT_ID,
        },
        http_client=http_client,
    )
    return _codex_credentials(document, fallback=credentials)


async def refresh_grok_credentials(
    credentials: GrokCredentials,
    *,
    http_client: httpx2.AsyncClient | None = None,
) -> GrokCredentials:
    if not credentials.refresh_token:
        raise CredentialRefreshError("grok", "The Grok account requires reauthentication.")
    client = http_client or httpx2.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)
    owned = http_client is None
    try:
        response = await client.get(
            f"{credentials.issuer.rstrip('/')}/.well-known/openid-configuration",
            headers={"Accept": "application/json"},
            auth=_no_auth,
            follow_redirects=False,
        )
        if response.status_code != 200:
            raise CredentialRefreshError("grok", "Grok OIDC discovery failed.")
        try:
            document = response.json()
        except ValueError:
            raise CredentialRefreshError("grok", "Grok OIDC discovery returned an invalid response.") from None
        endpoint = document.get("token_endpoint") if isinstance(document, dict) else None
        if not isinstance(endpoint, str):
            raise CredentialRefreshError("grok", "Grok OIDC discovery returned an invalid token endpoint.")
        try:
            parsed_endpoint = urlsplit(endpoint)
        except ValueError:
            raise CredentialRefreshError("grok", "Grok OIDC discovery returned an invalid token endpoint.") from None
        if parsed_endpoint.scheme != "https" or parsed_endpoint.hostname is None:
            raise CredentialRefreshError("grok", "Grok OIDC discovery returned an invalid token endpoint.")
        token = await _post_token(
            endpoint,
            {
                "grant_type": "refresh_token",
                "refresh_token": credentials.refresh_token,
                "client_id": credentials.client_id,
            },
            http_client=client,
        )
    finally:
        if owned:
            await client.aclose()
    access_token = _required_string(token, "access_token", "grok")
    refresh_token = token.get("refresh_token", credentials.refresh_token)
    expires_in = token.get("expires_in")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise CredentialRefreshError("grok", "The Grok token response has no refresh token.")
    if (
        isinstance(expires_in, bool)
        or not isinstance(expires_in, int | float)
        or expires_in <= 0
        or not math.isfinite(float(expires_in))
    ):
        raise CredentialRefreshError("grok", "The Grok token response has no valid expiry.")
    now = datetime.now(UTC)
    try:
        expires_at = now + timedelta(seconds=float(expires_in))
    except OverflowError:
        raise CredentialRefreshError("grok", "The Grok token response has no valid expiry.") from None
    return GrokCredentials(
        account_id=credentials.account_id,
        auth_mode=credentials.auth_mode,
        create_time=now,
        expires_at=expires_at,
        issuer=credentials.issuer,
        client_id=credentials.client_id,
        access_token=access_token,
        refresh_token=refresh_token,
    )


async def _post_token(
    url: str,
    form: Mapping[str, str],
    *,
    http_client: httpx2.AsyncClient | None = None,
) -> dict[str, object]:
    if http_client is None:
        async with httpx2.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            response = await client.post(url, data=dict(form), headers={"Accept": "application/json"})
    else:
        response = await http_client.post(
            url,
            data=dict(form),
            headers={"Accept": "application/json"},
            auth=_no_auth,
            follow_redirects=False,
        )
    if response.status_code != 200:
        raise CredentialRefreshError("model-oauth", f"OAuth token request failed with status {response.status_code}.")
    try:
        document = response.json()
    except ValueError:
        raise CredentialRefreshError("model-oauth", "OAuth token response is invalid.") from None
    if not isinstance(document, dict):
        raise CredentialRefreshError("model-oauth", "OAuth token response is invalid.")
    return document


def _codex_credentials(
    document: dict[str, object],
    *,
    fallback: CodexCredentials | None = None,
) -> CodexCredentials:
    access_token = _required_string(document, "access_token", "openai-codex")
    refresh_value = document.get("refresh_token", None if fallback is None else fallback.refresh_token)
    id_value = document.get("id_token", None if fallback is None else fallback.id_token)
    if not isinstance(refresh_value, str) or not refresh_value:
        raise CredentialRefreshError("openai-codex", "The Codex token response has no refresh token.")
    account_value = document.get("account_id")
    account_id = account_value if isinstance(account_value, str) and account_value else None
    if account_id is None and isinstance(id_value, str):
        account_id = _account_id(id_value)
    if account_id is None and fallback is not None:
        account_id = fallback.account_id
    expires_at = _jwt_expiry(access_token)
    if account_id is None or expires_at is None:
        raise CredentialRefreshError(
            "openai-codex", "The Codex token response is missing required identity or expiry data."
        )
    return CodexCredentials(
        account_id=account_id,
        expires_at=expires_at,
        access_token=access_token,
        refresh_token=refresh_value,
        id_token=id_value if isinstance(id_value, str) and id_value else None,
    )


def _required_string(document: dict[str, object], name: str, provider: str) -> str:
    value = document.get(name)
    if not isinstance(value, str) or not value:
        raise CredentialRefreshError(provider, f"The OAuth token response has no {name}.")
    return value


def _jwt_payload(token: str) -> dict[str, object] | None:
    try:
        segment = token.split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except (IndexError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _jwt_expiry(token: str) -> datetime | None:
    payload = _jwt_payload(token)
    value = None if payload is None else payload.get("exp")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _account_id(token: str) -> str | None:
    payload = _jwt_payload(token)
    if payload is None:
        return None
    nested = payload.get("https://api.openai.com/auth")
    if isinstance(nested, dict):
        value = nested.get("chatgpt_account_id")
        if isinstance(value, str) and value:
            return value
    for name in ("chatgpt_account_id", "account_id"):
        value = payload.get(name)
        if isinstance(value, str) and value:
            return value
    return None


__all__ = [
    "CodexOAuthFlow",
    "OAuthFlow",
    "refresh_codex_credentials",
    "refresh_grok_credentials",
]
