"""Codex login supplements for native shared-store interoperability.

Upstream owns browser PKCE/callback handling. Only device authorization and the
ID-token-preserving code exchange are supplied here; model refresh is upstream.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import anyio
import httpx2
from pydantic_ai.providers.openai_codex import OpenAICodexCredentials, OpenAICodexOAuthFlow

from ._jwt import jwt_payload
from .flow import json_object, oauth_request, positive_integer, post_token, required_string
from .models import CodexLoginResult, CredentialRefreshError, DeviceAuthorizationError

_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


class CodexLoginFlow(OpenAICodexOAuthFlow):
    """Use upstream PKCE/callback handling, retaining the native store's ID token.

    Pydantic AI's ordinary code exchange intentionally returns only model
    credentials. Shared native Codex stores additionally require the real ID
    token, so only the token exchange is specialized here.
    """

    def __init__(self) -> None:
        super().__init__()
        self._login: CodexLoginResult | None = None

    async def exchange_code(self, code: str) -> OpenAICodexCredentials:
        self._login = _codex_login_result(
            await post_token(
                _CODEX_TOKEN_URL,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": self.code_verifier,
                    "redirect_uri": self.redirect_uri,
                    "client_id": _CODEX_CLIENT_ID,
                },
            )
        )
        return self._login.credentials

    async def exchange_login_from_callback(self) -> CodexLoginResult:
        await self.exchange_code_from_callback()
        assert self._login is not None
        return self._login


@dataclass(frozen=True, slots=True)
class CodexDeviceAuthorization:
    """Codex's two-stage device grant (not the RFC 8628 token grant)."""

    verification_uri: str
    user_code: str
    expires_in: int
    interval: int
    _device_auth_id: str = field(repr=False)
    _expires_at: float = field(repr=False)
    _http_client: httpx2.AsyncClient | None = field(default=None, repr=False)

    async def wait_for_login(self) -> CodexLoginResult:
        try:
            with anyio.fail_after(max(0, self._expires_at - time.monotonic())):
                while True:
                    await anyio.sleep(self.interval)
                    response = await oauth_request(
                        "POST",
                        "https://auth.openai.com/api/accounts/deviceauth/token",
                        http_client=self._http_client,
                        json_data={"device_auth_id": self._device_auth_id, "user_code": self.user_code},
                    )
                    if response.status_code in {403, 404}:
                        continue
                    if response.status_code != 200:
                        raise CredentialRefreshError("openai-codex", "The Codex device authorization failed.")
                    document = json_object(response, provider="openai-codex")
                    tokens = await post_token(
                        _CODEX_TOKEN_URL,
                        {
                            "grant_type": "authorization_code",
                            "code": required_string(document, "authorization_code", "openai-codex"),
                            "code_verifier": required_string(document, "code_verifier", "openai-codex"),
                            "redirect_uri": "https://auth.openai.com/deviceauth/callback",
                            "client_id": _CODEX_CLIENT_ID,
                        },
                        http_client=self._http_client,
                    )
                    return _codex_login_result(tokens)
        except TimeoutError:
            raise DeviceAuthorizationError("openai-codex", "expired") from None


class CodexDeviceAuthorizationFlow:
    """Start the reviewed Codex device protocol without a local callback listener."""

    @staticmethod
    async def start(*, http_client: httpx2.AsyncClient | None = None) -> CodexDeviceAuthorization:
        response = await oauth_request(
            "POST",
            "https://auth.openai.com/api/accounts/deviceauth/usercode",
            http_client=http_client,
            json_data={"client_id": _CODEX_CLIENT_ID},
        )
        if response.status_code == 404:
            raise DeviceAuthorizationError("openai-codex", "unsupported")
        if response.status_code != 200:
            raise CredentialRefreshError("openai-codex", "The Codex device authorization request failed.")
        document = json_object(response, provider="openai-codex")
        user_code = required_string(document, "user_code", "openai-codex")
        if not all(c.isascii() and (c.isalnum() or c == "-") for c in user_code):
            raise CredentialRefreshError("openai-codex", "The Codex device user code is invalid.")
        interval_value = document.get("interval", 5)
        if isinstance(interval_value, str) and interval_value.isascii() and interval_value.isdecimal():
            interval_value = int(interval_value)
        interval = positive_integer(interval_value, "interval", provider="openai-codex")
        return CodexDeviceAuthorization(
            verification_uri="https://auth.openai.com/codex/device",
            user_code=user_code,
            expires_in=900,
            interval=min(interval, 900),
            _device_auth_id=required_string(document, "device_auth_id", "openai-codex"),
            _expires_at=time.monotonic() + 900,
            _http_client=http_client,
        )


def _codex_login_result(document: dict[str, object]) -> CodexLoginResult:
    """Retain login metadata without owning the model credential lifecycle."""
    access_token = required_string(document, "access_token", "openai-codex")
    refresh_token = required_string(document, "refresh_token", "openai-codex")
    account_value = document.get("account_id")
    account_id = account_value if isinstance(account_value, str) and account_value else None
    id_token = required_string(document, "id_token", "openai-codex")
    if account_id is None and isinstance(id_token, str):
        account_id = _account_id(id_token)
    if account_id is None:
        raise CredentialRefreshError("openai-codex", "The Codex token response has no account identity.")
    return CodexLoginResult(
        credentials=OpenAICodexCredentials(
            account_id=account_id, access_token=access_token, refresh_token=refresh_token
        ),
        id_token=id_token,
    )


def _account_id(token: str) -> str | None:
    payload = jwt_payload(token)
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


__all__ = ["CodexDeviceAuthorization", "CodexDeviceAuthorizationFlow", "CodexLoginFlow"]
