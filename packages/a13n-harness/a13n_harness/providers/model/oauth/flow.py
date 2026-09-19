"""Vendor-neutral OAuth: PKCE authorization-code flows and the loopback callback."""

from __future__ import annotations

import base64
import hashlib
import math
import secrets
import socket
import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit

import anyio
import anyio.to_thread
import httpx2
from pydantic_ai.exceptions import UserError

from .models import CredentialRefreshError

TIMEOUT = httpx2.Timeout(timeout=30, connect=5)
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def no_auth(request: httpx2.Request) -> httpx2.Request:
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


async def oauth_request(
    method: str,
    url: str,
    *,
    http_client: httpx2.AsyncClient | None,
    data: Mapping[str, str] | None = None,
    json_data: Mapping[str, str] | None = None,
) -> httpx2.Response:
    if http_client is None:
        async with httpx2.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            return await client.request(method, url, data=data, json=json_data, headers={"Accept": "application/json"})
    return await http_client.request(
        method,
        url,
        data=data,
        json=json_data,
        headers={"Accept": "application/json"},
        auth=no_auth,
        follow_redirects=False,
    )


def json_object(response: httpx2.Response, *, provider: str) -> dict[str, object]:
    try:
        document = response.json()
    except ValueError:
        raise CredentialRefreshError(provider, "The OAuth response is invalid.") from None
    if not isinstance(document, dict):
        raise CredentialRefreshError(provider, "The OAuth response is invalid.")
    return document


def append_query(url: str, params: Mapping[str, str]) -> str:
    separator = "&" if urlsplit(url).query else "?"
    return f"{url}{separator}{urlencode(params)}"


def available_loopback_redirect_uri() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    return f"http://127.0.0.1:{port}/callback"


def nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def scopes(values: Sequence[str]) -> tuple[str, ...]:
    scopes = tuple(values)
    if not scopes or len(set(scopes)) != len(scopes):
        raise ValueError("scopes must be non-empty and unique")
    if any(not isinstance(item, str) or not item or any(character.isspace() for character in item) for item in scopes):
        raise ValueError("each scope must be one non-empty token")
    return scopes


def positive_integer(value: object, name: str, *, provider: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CredentialRefreshError(provider, f"The OAuth response has no valid {name}.")
    return value


def positive_number(value: object, name: str, *, provider: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0 or not math.isfinite(float(value)):
        raise CredentialRefreshError(provider, f"The OAuth response has no valid {name}.")
    return float(value)


def expiry(now: datetime, expires_in: float, *, provider: str) -> datetime:
    try:
        return now + timedelta(seconds=expires_in)
    except OverflowError:
        raise CredentialRefreshError(provider, "The OAuth response has no valid expiry.") from None


async def post_token(
    url: str,
    form: Mapping[str, str],
    *,
    http_client: httpx2.AsyncClient | None = None,
) -> dict[str, object]:
    if http_client is None:
        async with httpx2.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            response = await client.post(url, data=dict(form), headers={"Accept": "application/json"})
    else:
        response = await http_client.post(
            url,
            data=dict(form),
            headers={"Accept": "application/json"},
            auth=no_auth,
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


def required_string(document: dict[str, object], name: str, provider: str) -> str:
    value = document.get(name)
    if not isinstance(value, str) or not value:
        raise CredentialRefreshError(provider, f"The OAuth token response has no {name}.")
    return value


def validate_loopback_redirect_uri(value: str) -> None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        parsed = None
    if (
        parsed is None
        or parsed.scheme != "http"
        or parsed.hostname not in LOOPBACK_HOSTS
        or parsed.port is None
        or not parsed.path.startswith("/")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise UserError("Grok OAuth redirect_uri must be an explicit HTTP loopback callback URL")
