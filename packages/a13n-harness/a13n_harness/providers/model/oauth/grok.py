"""Grok OIDC discovery, browser and device authorization, and credential refresh."""

from __future__ import annotations

import math
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import anyio
import anyio.to_thread
import httpx2
import jwt

from a13n_harness.http import outbound_tls_verify
from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError

from ._jwt import jwt_payload
from .flow import (
    LOOPBACK_HOSTS,
    TIMEOUT,
    OAuthFlow,
    append_query,
    available_loopback_redirect_uri,
    expiry,
    json_object,
    no_auth,
    nonempty,
    oauth_request,
    positive_integer,
    positive_number,
    post_token,
    required_string,
    validate_loopback_redirect_uri,
)
from .flow import (
    scopes as validated_scopes,
)
from .models import CredentialRefreshError, DeviceAuthorizationError, GrokCredentials, RefreshNotDispatched


def validated_url(value: str, *, name: str, allow_insecure_loopback: bool) -> str:
    """Reject anything but an HTTPS URL, or an explicit loopback HTTP URL when allowed."""

    policy = EndpointPolicy(require_https=not allow_insecure_loopback)
    try:
        normalized, hostname, _ = policy.validate_syntax(value)
    except EndpointPolicyError:
        raise CredentialRefreshError("grok", f"Grok OAuth returned an invalid {name} URL.") from None
    if urlsplit(normalized).scheme != "https" and hostname not in LOOPBACK_HOSTS:
        raise CredentialRefreshError("grok", f"Grok OAuth returned an invalid {name} URL.")
    return value


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
        self.client_id = nonempty(client_id, "client_id")
        self.scopes = validated_scopes(scopes)
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
        normalized_issuer = validated_url(
            issuer.rstrip("/"),
            name="issuer",
            allow_insecure_loopback=allow_insecure_loopback,
        )
        selected_redirect = redirect_uri or await anyio.to_thread.run_sync(available_loopback_redirect_uri)
        validate_loopback_redirect_uri(selected_redirect)
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
        return append_query(
            self._discovery.authorization_endpoint,
            self._merge_extra_params(params, extra_params),
        )

    async def exchange_code(self, code: str) -> GrokCredentials:
        document = await post_token(
            self._discovery.token_endpoint,
            {
                "grant_type": "authorization_code",
                "code": nonempty(code, "code"),
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
                raise DeviceAuthorizationError("grok", "expired")
            await anyio.sleep(min(interval, remaining))
            if time.monotonic() >= self._expires_at:
                raise DeviceAuthorizationError("grok", "expired")
            remaining = self._expires_at - time.monotonic()
            if remaining <= 0:
                raise DeviceAuthorizationError("grok", "expired")
            try:
                with anyio.fail_after(remaining):
                    response = await oauth_request(
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
                raise DeviceAuthorizationError("grok", "expired") from None
            document = json_object(response, provider="grok")
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
                raise DeviceAuthorizationError("grok", "denied")
            if error == "expired_token":
                raise DeviceAuthorizationError("grok", "expired")
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
        normalized_issuer = validated_url(
            issuer.rstrip("/"),
            name="issuer",
            allow_insecure_loopback=allow_insecure_loopback,
        )
        selected_client_id = nonempty(client_id, "client_id")
        form = {"client_id": selected_client_id, "scope": " ".join(validated_scopes(scopes))}
        if referrer is not None:
            form["referrer"] = nonempty(referrer, "referrer")
        response = await oauth_request(
            "POST",
            f"{normalized_issuer}/oauth2/device/code",
            http_client=http_client,
            data=form,
        )
        if response.status_code == 404:
            raise DeviceAuthorizationError("grok", "unsupported")
        if response.status_code != 200:
            raise CredentialRefreshError("grok", "The Grok device authorization request failed.")
        document = json_object(response, provider="grok")
        device_code = required_string(document, "device_code", "grok")
        user_code = required_string(document, "user_code", "grok")
        if not all(character.isascii() and (character.isalnum() or character == "-") for character in user_code):
            raise CredentialRefreshError("grok", "The Grok device authorization returned an invalid user code.")
        verification_uri = validated_url(
            required_string(document, "verification_uri", "grok"),
            name="verification_uri",
            allow_insecure_loopback=allow_insecure_loopback,
        )
        complete_value = document.get("verification_uri_complete")
        verification_uri_complete = (
            None
            if complete_value is None
            else validated_url(
                required_string(document, "verification_uri_complete", "grok"),
                name="verification_uri_complete",
                allow_insecure_loopback=allow_insecure_loopback,
            )
        )
        expires_in = positive_integer(document.get("expires_in"), "expires_in", provider="grok")
        interval_value = document.get("interval", 5)
        interval = positive_integer(interval_value, "interval", provider="grok")
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


async def refresh_grok_credentials(
    credentials: GrokCredentials,
    *,
    http_client: httpx2.AsyncClient | None = None,
) -> GrokCredentials:
    if not credentials.refresh_token:
        raise RefreshNotDispatched("grok", "The Grok account requires reauthentication.")
    issuer = validated_url(
        credentials.issuer.rstrip("/"),
        name="issuer",
        allow_insecure_loopback=False,
    )
    client = http_client or httpx2.AsyncClient(verify=outbound_tls_verify(), timeout=TIMEOUT, follow_redirects=False)
    owned = http_client is None
    try:
        try:
            response = await client.get(
                f"{issuer}/.well-known/openid-configuration",
                headers={"Accept": "application/json"},
                auth=no_auth,
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
            endpoint = validated_url(
                endpoint_value,
                name="token_endpoint",
                allow_insecure_loopback=False,
            )
        except (CredentialRefreshError, httpx2.HTTPError, ValueError) as error:
            raise RefreshNotDispatched(
                "grok",
                str(error)
                if isinstance(error, CredentialRefreshError)
                else "Grok discovery failed before token dispatch.",
            ) from None
        token = await post_token(
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
    access_token = required_string(token, "access_token", "grok")
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


async def _discover_grok(
    issuer: str,
    *,
    http_client: httpx2.AsyncClient | None,
    allow_insecure_loopback: bool,
) -> _GrokDiscovery:
    response = await oauth_request(
        "GET",
        f"{issuer}/.well-known/openid-configuration",
        http_client=http_client,
    )
    if response.status_code != 200:
        raise CredentialRefreshError("grok", "Grok OIDC discovery failed.")
    document = json_object(response, provider="grok")
    discovered_issuer = validated_url(
        required_string(document, "issuer", "grok"),
        name="issuer",
        allow_insecure_loopback=allow_insecure_loopback,
    ).rstrip("/")
    if discovered_issuer != issuer:
        raise CredentialRefreshError("grok", "Grok OIDC discovery returned a different issuer.")
    authorization_endpoint = validated_url(
        required_string(document, "authorization_endpoint", "grok"),
        name="authorization_endpoint",
        allow_insecure_loopback=allow_insecure_loopback,
    )
    token_endpoint = validated_url(
        required_string(document, "token_endpoint", "grok"),
        name="token_endpoint",
        allow_insecure_loopback=allow_insecure_loopback,
    )
    jwks_uri = validated_url(
        required_string(document, "jwks_uri", "grok"),
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
    access_token = required_string(document, "access_token", "grok")
    refresh_token = required_string(document, "refresh_token", "grok")
    id_token = required_string(document, "id_token", "grok")
    expires_in = positive_number(document.get("expires_in"), "expires_in", provider="grok")
    response = await oauth_request("GET", discovery.jwks_uri, http_client=http_client)
    if response.status_code != 200:
        raise CredentialRefreshError("grok", "Grok OIDC signing keys could not be loaded.")
    jwks = json_object(response, provider="grok")
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
        expires_at=expiry(now, expires_in, provider="grok"),
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
    access_token = required_string(document, "access_token", "grok")
    refresh_value = document.get("refresh_token")
    refresh_token = refresh_value if isinstance(refresh_value, str) and refresh_value else None
    expires_in = positive_number(document.get("expires_in"), "expires_in", provider="grok")
    id_value = document.get("id_token")
    id_payload = jwt_payload(id_value) if isinstance(id_value, str) else None
    access_payload = jwt_payload(access_token)
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
        expires_at=expiry(now, expires_in, provider="grok"),
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
