"""Host-owned device polling and loopback authorization-code validation."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from urllib.parse import parse_qsl, urlsplit

import httpx2
from anyio import create_task_group, create_tcp_listener, current_time, fail_after, sleep
from anyio.abc import SocketAttribute

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.model_accounts.callback import receive_callback

from .catalog import Registration
from .sdk import (
    AuthorizedAccount,
    CallbackChallenge,
    DeviceChallenge,
    DevicePollSlowDown,
    LoginMethod,
    OAuthContext,
    PkceChallenge,
    https_url,
    origin,
)
from .transport import provider_http_client

_MAX_LOGIN_SECONDS = 900


def create_pkce() -> PkceChallenge:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return PkceChallenge(verifier, challenge, secrets.token_urlsafe(32), secrets.token_urlsafe(32))


def _params(query: str) -> dict[str, str]:
    if re.search(r"%(?![0-9a-fA-F]{2})", query):
        raise ValueError("Invalid query encoding")
    pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, errors="strict", max_num_fields=64)
    parameters = dict(pairs)
    if len(parameters) != len(pairs) or "" in parameters:
        raise ValueError("Each callback parameter must occur once")
    return parameters


def callback_parameters(url: str, redirect_uri: str, pkce: PkceChallenge) -> dict[str, str]:
    """Validate the submitted URL as data, without resolving or fetching it."""
    try:
        if (
            len(url) > 16384
            or not url.startswith("http://")
            or "#" in url
            or any(ord(ch) <= 32 or ord(ch) == 127 or ch == "\\" for ch in url)
        ):
            raise ValueError
        actual = urlsplit(url)
        expected = urlsplit(redirect_uri)
        if (actual.scheme, actual.netloc, actual.path) != (expected.scheme, expected.netloc, expected.path):
            raise ValueError
        parameters = _params(actual.query)
        state = parameters.get("state", "")
        if not state.isascii() or not secrets.compare_digest(state, pkce.state):
            raise ValueError
        if "error" not in parameters and not parameters.get("code"):
            raise ValueError
    except ValueError:
        raise HarnessUiError(
            "The callback URL does not match this sign-in. Paste the complete URL from the browser address bar.",
            code="login_callback_invalid",
        ) from None
    return parameters


async def _provider_call[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except (DevicePollSlowDown, httpx2.TimeoutException):
        raise
    except Exception:
        raise HarnessUiError("Model Provider authorization failed.", code="oauth_login_failed") from None


async def authorize_plugin(
    registration: Registration,
    method: LoginMethod,
    present: Callable[..., None],
    receive_parameters: Callable,  # awaitable accepting a validator; the session owns the input channel
) -> AuthorizedAccount:
    plugin = registration.plugin
    decl = registration.declaration
    deadline = current_time() + _MAX_LOGIN_SECONDS
    with fail_after(_MAX_LOGIN_SECONDS):
        async with provider_http_client(decl, oauth=True) as client:
            if method == "device":
                ctx = OAuthContext(client)
                challenge = await _provider_call(plugin.start_device_login(ctx))
                if not isinstance(challenge, DeviceChallenge):
                    raise ValueError("Expected a device challenge")
                https_url(challenge.verification_url)
                ttl = min(challenge.expires_in, deadline - current_time())
                present(verification_url=challenge.verification_url, user_code=challenge.user_code, expires_in=int(ttl))
                interval = max(1.0, challenge.poll_interval)
                with fail_after(ttl):
                    while True:
                        await sleep(interval)
                        try:
                            account = await _provider_call(plugin.poll_device_login(ctx, challenge))
                        except DevicePollSlowDown:
                            interval += 5
                            continue
                        except httpx2.TimeoutException:
                            interval *= 2
                            continue
                        if account is not None:
                            return account
            spec = decl.loopback_callback
            assert spec is not None
            pkce = create_pkce()
            async with AsyncExitStack() as resources:
                port = spec.port
                listener = None
                if method == "browser" or port == 0:
                    try:
                        # Use the numeric bind even when the registered URI uses localhost.
                        listener = await create_tcp_listener(local_host="127.0.0.1", local_port=port)
                    except OSError:
                        raise HarnessUiError(
                            "Could not open the loopback callback port. Close the process using it or choose manual callback.",
                            code="login_loopback_unavailable",
                        ) from None
                    port = listener.extra(SocketAttribute.local_address)[1]
                    if method == "manual_callback":
                        await listener.aclose()
                        listener = None
                    else:
                        await resources.enter_async_context(listener)
                redirect = f"http://{spec.host}:{port}{spec.path}"
                ctx = OAuthContext(client, redirect, pkce)
                challenge = await _provider_call(plugin.begin_callback_login(ctx))
                if not isinstance(challenge, CallbackChallenge):
                    raise ValueError("Expected a callback challenge")
                url = https_url(challenge.authorization_url)
                if origin(url) not in {origin(https_url(item)) for item in decl.allowed_origins}:
                    raise ValueError("The authorization URL requires a declared HTTPS origin")
                fields = _params(urlsplit(challenge.authorization_url).query)
                if any(
                    fields.get(key) != value
                    for key, value in {
                        "redirect_uri": redirect,
                        "state": pkce.state,
                        "code_challenge_method": "S256",
                        "code_challenge": pkce.challenge_s256,
                    }.items()
                ) or ("nonce" in fields and fields["nonce"] != pkce.nonce):
                    raise ValueError("The authorization URL must use the Host redirect and PKCE challenge")
                ttl = min(challenge.expires_in, deadline - current_time())
                with fail_after(ttl):
                    params: dict[str, str] = {}
                    async with create_task_group() as tasks:

                        def validate(url: str) -> dict[str, str]:
                            return callback_parameters(url, redirect, pkce)

                        present(verification_url=challenge.authorization_url, expires_in=int(ttl))
                        params = await receive_parameters(
                            validate,
                            lambda submit: (
                                tasks.start_soon(
                                    listener.serve, lambda stream: receive_callback(stream, redirect, submit)
                                )
                                if listener is not None
                                else None
                            ),
                        )
                        tasks.cancel_scope.cancel()
                    if "error" in params:
                        raise HarnessUiError(
                            "The Model Provider declined authorization.", code="oauth_authorization_denied"
                        )
                    return await _provider_call(plugin.complete_callback_login(ctx, challenge, params))
