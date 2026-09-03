"""Provider-compatible OAuth exchange and refresh primitives."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
import socket
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit

import anyio
import anyio.to_thread
import httpx2
import jwt
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
            overridden = params.keys() & extra_params.keys()
            if overridden:
                raise UserError(f"extra_params cannot override OAuth parameter: {sorted(overridden)[0]}")
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

    async def exchange_code_from_callback(self, *, timeout_seconds: float | None = None) -> CredentialsT:
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
            if timeout_seconds is None:
                await anyio.to_thread.run_sync(serve, abandon_on_cancel=True)
            else:
                if timeout_seconds <= 0:
                    raise ValueError("timeout_seconds must be positive")
                with anyio.fail_after(timeout_seconds):
                    await anyio.to_thread.run_sync(serve, abandon_on_cancel=True)
        except TimeoutError:
            raise UserError("Authorization callback timed out.") from None
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


@dataclass(frozen=True, slots=True)
class _GrokDiscovery:
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    signing_algorithms: tuple[str, ...]


class GrokOAuthFlow(OAuthFlow[GrokCredentials]):
    """Discovered Grok OIDC authorization-code flow with verified identity."""

    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        scopes: Sequence[str],
        redirect_uri: str,
        discovery: _GrokDiscovery,
        referrer: str | None,
        http_client: httpx2.AsyncClient | None,
        state: str | None = None,
        nonce: str | None = None,
    ) -> None:
        super().__init__(redirect_uri=redirect_uri, state=state)
        self.issuer = issuer.rstrip("/")
        self.client_id = _nonempty(client_id, "client_id")
        self.scopes = _scopes(scopes)
        self.nonce = nonce or secrets.token_urlsafe(16)
        self._discovery = discovery
        self._referrer = referrer
        self._http_client = http_client

    @classmethod
    async def discover(
        cls,
        *,
        issuer: str,
        client_id: str,
        scopes: Sequence[str],
        redirect_uri: str | None = None,
        referrer: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
        allow_insecure_loopback: bool = False,
    ) -> GrokOAuthFlow:
        normalized_issuer = _validated_url(
            issuer.rstrip("/"),
            name="issuer",
            allow_insecure_loopback=allow_insecure_loopback,
        )
        selected_redirect = redirect_uri or await anyio.to_thread.run_sync(_available_loopback_redirect_uri)
        _validate_loopback_redirect_uri(selected_redirect)
        discovery = await _discover_grok(
            normalized_issuer,
            http_client=http_client,
            allow_insecure_loopback=allow_insecure_loopback,
        )
        return cls(
            issuer=normalized_issuer,
            client_id=client_id,
            scopes=scopes,
            redirect_uri=selected_redirect,
            discovery=discovery,
            referrer=referrer,
            http_client=http_client,
        )

    def authorization_url(
        self,
        *,
        scope: str | None = None,
        extra_params: Mapping[str, str] | None = None,
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": scope if scope is not None else " ".join(self.scopes),
            "state": self.state,
            "nonce": self.nonce,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256",
        }
        if self._referrer is not None:
            params["referrer"] = self._referrer
        return _append_query(
            self._discovery.authorization_endpoint,
            self._merge_extra_params(params, extra_params),
        )

    async def exchange_code(self, code: str) -> GrokCredentials:
        document = await _post_token(
            self._discovery.token_endpoint,
            {
                "grant_type": "authorization_code",
                "code": _nonempty(code, "code"),
                "code_verifier": self.code_verifier,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
            },
            http_client=self._http_client,
        )
        return await _grok_browser_credentials(
            document,
            issuer=self.issuer,
            client_id=self.client_id,
            nonce=self.nonce,
            discovery=self._discovery,
            http_client=self._http_client,
        )


@dataclass(frozen=True, slots=True)
class GrokDeviceAuthorization:
    """One bounded RFC 8628 device grant with secret code kept process-local."""

    issuer: str
    client_id: str
    verification_uri: str
    verification_uri_complete: str | None
    user_code: str
    expires_in: int
    interval: int
    _device_code: str = field(repr=False)
    _token_endpoint: str = field(repr=False)
    _expires_at: float = field(repr=False)
    _http_client: httpx2.AsyncClient | None = field(default=None, repr=False)

    async def wait_for_credentials(self) -> GrokCredentials:
        interval = float(self.interval)
        while True:
            remaining = self._expires_at - time.monotonic()
            if remaining <= 0:
                raise CredentialRefreshError("grok", "The Grok device code expired.")
            await anyio.sleep(min(interval, remaining))
            if time.monotonic() >= self._expires_at:
                raise CredentialRefreshError("grok", "The Grok device code expired.")
            remaining = self._expires_at - time.monotonic()
            if remaining <= 0:
                raise CredentialRefreshError("grok", "The Grok device code expired.")
            try:
                with anyio.fail_after(remaining):
                    response = await _request(
                        "POST",
                        self._token_endpoint,
                        http_client=self._http_client,
                        data={
                            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                            "device_code": self._device_code,
                            "client_id": self.client_id,
                        },
                    )
            except TimeoutError:
                raise CredentialRefreshError("grok", "The Grok device code expired.") from None
            document = _json_object(response, provider="grok")
            if response.status_code == 200:
                return _grok_direct_credentials(
                    document,
                    issuer=self.issuer,
                    client_id=self.client_id,
                )
            error = document.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error == "access_denied":
                raise CredentialRefreshError("grok", "The Grok device authorization was denied.")
            if error == "expired_token":
                raise CredentialRefreshError("grok", "The Grok device code expired.")
            raise CredentialRefreshError("grok", "The Grok device token request failed.")


class GrokDeviceAuthorizationFlow:
    """Start a provider-compatible Grok RFC 8628 device authorization."""

    @staticmethod
    async def start(
        *,
        issuer: str,
        client_id: str,
        scopes: Sequence[str],
        referrer: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
        allow_insecure_loopback: bool = False,
    ) -> GrokDeviceAuthorization:
        normalized_issuer = _validated_url(
            issuer.rstrip("/"),
            name="issuer",
            allow_insecure_loopback=allow_insecure_loopback,
        )
        selected_client_id = _nonempty(client_id, "client_id")
        form = {"client_id": selected_client_id, "scope": " ".join(_scopes(scopes))}
        if referrer is not None:
            form["referrer"] = _nonempty(referrer, "referrer")
        response = await _request(
            "POST",
            f"{normalized_issuer}/oauth2/device/code",
            http_client=http_client,
            data=form,
        )
        if response.status_code != 200:
            raise CredentialRefreshError("grok", "The Grok device authorization request failed.")
        document = _json_object(response, provider="grok")
        device_code = _required_string(document, "device_code", "grok")
        user_code = _required_string(document, "user_code", "grok")
        if not all(character.isascii() and (character.isalnum() or character == "-") for character in user_code):
            raise CredentialRefreshError("grok", "The Grok device authorization returned an invalid user code.")
        verification_uri = _validated_url(
            _required_string(document, "verification_uri", "grok"),
            name="verification_uri",
            allow_insecure_loopback=allow_insecure_loopback,
        )
        complete_value = document.get("verification_uri_complete")
        verification_uri_complete = (
            None
            if complete_value is None
            else _validated_url(
                _required_string(document, "verification_uri_complete", "grok"),
                name="verification_uri_complete",
                allow_insecure_loopback=allow_insecure_loopback,
            )
        )
        expires_in = _positive_integer(document.get("expires_in"), "expires_in", provider="grok")
        interval_value = document.get("interval", 5)
        interval = _positive_integer(interval_value, "interval", provider="grok")
        return GrokDeviceAuthorization(
            issuer=normalized_issuer,
            client_id=selected_client_id,
            verification_uri=verification_uri,
            verification_uri_complete=verification_uri_complete,
            user_code=user_code,
            expires_in=expires_in,
            interval=interval,
            _device_code=device_code,
            _token_endpoint=f"{normalized_issuer}/oauth2/token",
            _expires_at=time.monotonic() + expires_in,
            _http_client=http_client,
        )


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
    issuer = _validated_url(
        credentials.issuer.rstrip("/"),
        name="issuer",
        allow_insecure_loopback=False,
    )
    client = http_client or httpx2.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)
    owned = http_client is None
    try:
        response = await client.get(
            f"{issuer}/.well-known/openid-configuration",
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
        if not isinstance(document, dict):
            raise CredentialRefreshError("grok", "Grok OIDC discovery returned an invalid response.")
        discovered_issuer = document.get("issuer")
        if not isinstance(discovered_issuer, str) or discovered_issuer.rstrip("/") != issuer:
            raise CredentialRefreshError("grok", "Grok OIDC discovery returned a different issuer.")
        endpoint_value = document.get("token_endpoint")
        if not isinstance(endpoint_value, str):
            raise CredentialRefreshError("grok", "Grok OIDC discovery returned an invalid token endpoint.")
        endpoint = _validated_url(
            endpoint_value,
            name="token_endpoint",
            allow_insecure_loopback=False,
        )
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


async def _discover_grok(
    issuer: str,
    *,
    http_client: httpx2.AsyncClient | None,
    allow_insecure_loopback: bool,
) -> _GrokDiscovery:
    response = await _request(
        "GET",
        f"{issuer}/.well-known/openid-configuration",
        http_client=http_client,
    )
    if response.status_code != 200:
        raise CredentialRefreshError("grok", "Grok OIDC discovery failed.")
    document = _json_object(response, provider="grok")
    discovered_issuer = _validated_url(
        _required_string(document, "issuer", "grok"),
        name="issuer",
        allow_insecure_loopback=allow_insecure_loopback,
    ).rstrip("/")
    if discovered_issuer != issuer:
        raise CredentialRefreshError("grok", "Grok OIDC discovery returned a different issuer.")
    authorization_endpoint = _validated_url(
        _required_string(document, "authorization_endpoint", "grok"),
        name="authorization_endpoint",
        allow_insecure_loopback=allow_insecure_loopback,
    )
    token_endpoint = _validated_url(
        _required_string(document, "token_endpoint", "grok"),
        name="token_endpoint",
        allow_insecure_loopback=allow_insecure_loopback,
    )
    jwks_uri = _validated_url(
        _required_string(document, "jwks_uri", "grok"),
        name="jwks_uri",
        allow_insecure_loopback=allow_insecure_loopback,
    )
    algorithms_value = document.get("id_token_signing_alg_values_supported")
    if algorithms_value is None:
        algorithms = _ALLOWED_GROK_ID_TOKEN_ALGORITHMS
    elif isinstance(algorithms_value, list) and all(isinstance(item, str) for item in algorithms_value):
        algorithms = tuple(item for item in algorithms_value if item in _ALLOWED_GROK_ID_TOKEN_ALGORITHMS)
    else:
        raise CredentialRefreshError("grok", "Grok OIDC discovery returned invalid signing algorithms.")
    if not algorithms:
        raise CredentialRefreshError("grok", "Grok OIDC discovery has no supported signing algorithm.")
    return _GrokDiscovery(
        authorization_endpoint=authorization_endpoint,
        token_endpoint=token_endpoint,
        jwks_uri=jwks_uri,
        signing_algorithms=algorithms,
    )


async def _grok_browser_credentials(
    document: dict[str, object],
    *,
    issuer: str,
    client_id: str,
    nonce: str,
    discovery: _GrokDiscovery,
    http_client: httpx2.AsyncClient | None,
) -> GrokCredentials:
    access_token = _required_string(document, "access_token", "grok")
    refresh_token = _required_string(document, "refresh_token", "grok")
    id_token = _required_string(document, "id_token", "grok")
    expires_in = _positive_number(document.get("expires_in"), "expires_in", provider="grok")
    response = await _request("GET", discovery.jwks_uri, http_client=http_client)
    if response.status_code != 200:
        raise CredentialRefreshError("grok", "Grok OIDC signing keys could not be loaded.")
    jwks = _json_object(response, provider="grok")
    try:
        header = jwt.get_unverified_header(id_token)
        algorithm = header.get("alg")
        key_id = header.get("kid")
        if algorithm not in discovery.signing_algorithms or not isinstance(key_id, str) or not key_id:
            raise ValueError("unsupported ID token header")
        keys = jwks.get("keys")
        if not isinstance(keys, list):
            raise ValueError("invalid JWKS")
        key_document = next(item for item in keys if isinstance(item, dict) and item.get("kid") == key_id)
        signing_key = jwt.PyJWK.from_dict(key_document, algorithm=algorithm).key
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=[algorithm],
            audience=client_id,
            issuer=issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except (KeyError, StopIteration, TypeError, ValueError, jwt.PyJWTError):
        raise CredentialRefreshError("grok", "The Grok ID token could not be verified.") from None
    if claims.get("nonce") != nonce:
        raise CredentialRefreshError("grok", "The Grok ID token nonce does not match the authorization flow.")
    account_id = claims.get("sub")
    if not isinstance(account_id, str) or not account_id:
        raise CredentialRefreshError("grok", "The Grok ID token has no account identity.")
    now = datetime.now(UTC)
    return GrokCredentials(
        account_id=account_id,
        auth_mode="oidc",
        create_time=now,
        expires_at=_expiry(now, expires_in, provider="grok"),
        issuer=issuer,
        client_id=client_id,
        access_token=access_token,
        refresh_token=refresh_token,
    )


def _grok_direct_credentials(
    document: dict[str, object],
    *,
    issuer: str,
    client_id: str,
) -> GrokCredentials:
    access_token = _required_string(document, "access_token", "grok")
    refresh_value = document.get("refresh_token")
    refresh_token = refresh_value if isinstance(refresh_value, str) and refresh_value else None
    expires_in = _positive_number(document.get("expires_in"), "expires_in", provider="grok")
    id_value = document.get("id_token")
    id_payload = _jwt_payload(id_value) if isinstance(id_value, str) else None
    access_payload = _jwt_payload(access_token)
    account_id = _selected_grok_principal(access_payload)
    if account_id is None and id_payload is not None:
        subject = id_payload.get("sub")
        account_id = subject if isinstance(subject, str) and subject else None
    if account_id is None and access_payload is not None:
        subject = access_payload.get("sub")
        account_id = subject if isinstance(subject, str) and subject else None
    if account_id is None:
        raise CredentialRefreshError("grok", "The Grok device token response has no account identity.")
    now = datetime.now(UTC)
    return GrokCredentials(
        account_id=account_id,
        auth_mode="oidc",
        create_time=now,
        expires_at=_expiry(now, expires_in, provider="grok"),
        issuer=issuer,
        client_id=client_id,
        access_token=access_token,
        refresh_token=refresh_token,
    )


def _selected_grok_principal(payload: dict[str, object] | None) -> str | None:
    if payload is None:
        return None
    value = payload.get("principal_id", payload.get("principalId"))
    return value if isinstance(value, str) and value else None


async def _request(
    method: str,
    url: str,
    *,
    http_client: httpx2.AsyncClient | None,
    data: Mapping[str, str] | None = None,
) -> httpx2.Response:
    if http_client is None:
        async with httpx2.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            return await client.request(method, url, data=data, headers={"Accept": "application/json"})
    return await http_client.request(
        method,
        url,
        data=data,
        headers={"Accept": "application/json"},
        auth=_no_auth,
        follow_redirects=False,
    )


def _json_object(response: httpx2.Response, *, provider: str) -> dict[str, object]:
    try:
        document = response.json()
    except ValueError:
        raise CredentialRefreshError(provider, "The OAuth response is invalid.") from None
    if not isinstance(document, dict):
        raise CredentialRefreshError(provider, "The OAuth response is invalid.")
    return document


def _validated_url(value: str, *, name: str, allow_insecure_loopback: bool) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        parsed = None
    if (
        parsed is None
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (
            parsed.scheme != "https"
            and not (
                allow_insecure_loopback
                and parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        )
    ):
        raise CredentialRefreshError("grok", f"Grok OAuth returned an invalid {name} URL.")
    return value


def _validate_loopback_redirect_uri(value: str) -> None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        parsed = None
    if (
        parsed is None
        or parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.port is None
        or not parsed.path.startswith("/")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise UserError("Grok OAuth redirect_uri must be an explicit HTTP loopback callback URL")


def _append_query(url: str, params: Mapping[str, str]) -> str:
    separator = "&" if urlsplit(url).query else "?"
    return f"{url}{separator}{urlencode(params)}"


def _available_loopback_redirect_uri() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    return f"http://127.0.0.1:{port}/callback"


def _nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _scopes(values: Sequence[str]) -> tuple[str, ...]:
    scopes = tuple(values)
    if not scopes or len(set(scopes)) != len(scopes):
        raise ValueError("scopes must be non-empty and unique")
    if any(not isinstance(item, str) or not item or any(character.isspace() for character in item) for item in scopes):
        raise ValueError("each scope must be one non-empty token")
    return scopes


def _positive_integer(value: object, name: str, *, provider: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CredentialRefreshError(provider, f"The OAuth response has no valid {name}.")
    return value


def _positive_number(value: object, name: str, *, provider: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0 or not math.isfinite(float(value)):
        raise CredentialRefreshError(provider, f"The OAuth response has no valid {name}.")
    return float(value)


def _expiry(now: datetime, expires_in: float, *, provider: str) -> datetime:
    try:
        return now + timedelta(seconds=expires_in)
    except OverflowError:
        raise CredentialRefreshError(provider, "The OAuth response has no valid expiry.") from None


_ALLOWED_GROK_ID_TOKEN_ALGORITHMS = (
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "EdDSA",
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
    "GrokDeviceAuthorization",
    "GrokDeviceAuthorizationFlow",
    "GrokOAuthFlow",
    "OAuthFlow",
    "refresh_codex_credentials",
    "refresh_grok_credentials",
]
