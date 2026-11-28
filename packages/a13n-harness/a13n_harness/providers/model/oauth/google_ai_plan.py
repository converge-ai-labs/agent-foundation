"""Google AI Plan authorization and sender-constrained refresh grants."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from importlib.metadata import version
from typing import Protocol, Self, runtime_checkable
from urllib.parse import parse_qsl, urlsplit

import httpx2
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, ValidationError

from .flow import OAuthFlow, append_query, expiry, no_auth, positive_integer, required_string
from .models import CredentialRefreshError, ModelAuthenticationError

SCHEME = "google-ai-plan"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
QUOTA_SCOPE = "https://www.googleapis.com/auth/generative-language.peruserquota"
CALLBACK_PATH = "/oauth2callback"
LOGIN_TIMEOUT = 600
_RESPONSE_BYTES = 256 * 1024


def user_agent() -> str:
    return f"agent-foundation/{version('a13n-harness')}"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _sha256(text: str) -> str:
    return _b64(hashlib.sha256(text.encode()).digest())


class DPoPKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    kty: str = Field(pattern="^EC$")
    crv: str = Field(pattern="^P-256$")
    x: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    y: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    d: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)

    @classmethod
    def generate(cls) -> Self:
        numbers = ec.generate_private_key(ec.SECP256R1()).private_numbers()
        return cls(
            kty="EC",
            crv="P-256",
            x=_b64(numbers.public_numbers.x.to_bytes(32, "big")),
            y=_b64(numbers.public_numbers.y.to_bytes(32, "big")),
            d=_b64(numbers.private_value.to_bytes(32, "big")),
        )

    def proof(self, *, jti: str, iat: int, nonce: str | None) -> str:
        def number(value: str) -> int:
            return int.from_bytes(base64.urlsafe_b64decode(value + "="), "big")

        try:
            public = ec.EllipticCurvePublicNumbers(number(self.x), number(self.y), ec.SECP256R1())
            private = ec.EllipticCurvePrivateNumbers(number(self.d), public).private_key()
        except ValueError:
            raise CredentialRefreshError(SCHEME, "The Google DPoP key is invalid. Sign in again.") from None
        claims: dict[str, str | int] = {"htm": "POST", "htu": TOKEN_URL, "jti": jti, "iat": iat}
        if nonce:
            claims["nonce"] = nonce
        return jwt.encode(
            claims,
            private,
            algorithm="ES256",
            headers={
                "typ": "dpop+jwt",
                "jwk": self.model_dump(exclude={"d"}),
            },
        )


class GoogleAIPlanClient(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    client_id: str = Field(min_length=1, max_length=256)
    client_secret: SecretStr = Field(repr=False)

    @classmethod
    def configured(cls, client_id: str | None, client_secret: str | None) -> Self:
        if not client_id or not client_secret or not client_id.strip() or not client_secret.strip():
            raise ModelAuthenticationError(SCHEME, "Configure an approved Google AI Plan OAuth client ID and secret.")
        return cls(client_id=client_id, client_secret=SecretStr(client_secret))


class GoogleAIPlanCredentials(BaseModel):
    """Complete Host-owned credential envelope; serialize only to private storage."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    account_id: str = Field(min_length=1, max_length=255)
    client_id: str = Field(min_length=1, max_length=256)
    access_token: str = Field(min_length=1, repr=False)
    refresh_token: str = Field(min_length=1, repr=False)
    expires_at: AwareDatetime
    dpop_jwk: DPoPKey = Field(repr=False)
    dpop_nonce: str | None = Field(default=None, repr=False)


@runtime_checkable
class GoogleAIPlanCredentialSource(Protocol):
    """Host source bound to one Google account and authorization generation."""

    async def current(self) -> GoogleAIPlanCredentials: ...

    async def refresh(self, expected: GoogleAIPlanCredentials) -> GoogleAIPlanCredentials: ...


def _callback_target(url: str) -> tuple[str, str, str]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not parsed.port
        or parsed.netloc != f"127.0.0.1:{parsed.port}"
        or parsed.path != CALLBACK_PATH
        or "#" in url
        or any(character in url for character in "\r\n\t")
    ):
        raise ValueError("Use an explicit 127.0.0.1 Google OAuth loopback callback URL.")
    return parsed.scheme, parsed.netloc, parsed.path


def validate_redirect_uri(value: str) -> str:
    _callback_target(value)
    if "?" in value:
        raise ValueError("The Google OAuth redirect URI cannot contain query parameters.")
    return value


def callback_code(value: str, redirect_uri: str, state: str) -> str:
    """Validate the complete callback without following or fetching it."""
    try:
        if len(value) > 8192 or _callback_target(value) != _callback_target(redirect_uri):
            raise ValueError
        query = urlsplit(value).query
        if re.search(r"%(?![0-9a-fA-F]{2})", query):
            raise ValueError
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, errors="strict", max_num_fields=32)
        fields = dict(pairs)
        if len(fields) != len(pairs) or not secrets.compare_digest(fields.get("state", "").encode(), state.encode()):
            raise ValueError
        if "error" not in fields and fields.get("code"):
            return fields["code"]
    except ValueError:
        pass
    raise ModelAuthenticationError(SCHEME, "The Google OAuth callback was rejected or does not match this login.")


class GoogleAIPlanFlow(OAuthFlow[GoogleAIPlanCredentials]):
    def __init__(
        self,
        registration: GoogleAIPlanClient,
        http_client: httpx2.AsyncClient | None,
        *,
        redirect_uri: str,
        state: str | None = None,
        code_verifier: str | None = None,
        nonce: str | None = None,
    ) -> None:
        super().__init__(redirect_uri=validate_redirect_uri(redirect_uri), state=state)
        if code_verifier is not None:
            self.code_verifier = code_verifier
        self.nonce = nonce or secrets.token_urlsafe(32)
        self.registration = registration
        self.http_client = http_client

    def authorization_url(self, *, scope: str | None = None, extra_params: Mapping[str, str] | None = None) -> str:
        if scope is not None or extra_params:
            raise ValueError("Google AI Plan authorization parameters follow the partner protocol.")
        return append_query(
            AUTHORIZE_URL,
            {
                "response_type": "code",
                "client_id": self.registration.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": f"openid email profile {QUOTA_SCOPE}",
                "state": self.state,
                "nonce": self.nonce,
                "code_challenge": self.code_challenge,
                "code_challenge_method": "S256",
            },
        )

    def _loopback_result(self, target: str) -> dict[str, str]:
        if not target.startswith(CALLBACK_PATH + "?"):
            raise ValueError("Invalid Google OAuth callback target.")
        callback_url = self.redirect_uri + target[len(CALLBACK_PATH) :]
        try:
            return {"code": callback_code(callback_url, self.redirect_uri, self.state)}
        except ModelAuthenticationError:
            raise ValueError("Invalid Google OAuth callback target.") from None

    async def exchange_callback(self, callback_url: str) -> GoogleAIPlanCredentials:
        return await self.exchange_code(callback_code(callback_url, self.redirect_uri, self.state))

    async def exchange_code(self, code: str) -> GoogleAIPlanCredentials:
        if self.http_client is None:
            raise ValueError("Google AI Plan exchange requires a Host HTTP client.")
        key = DPoPKey.generate()
        response, document = await _grant(
            self.registration,
            self.http_client,
            key,
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": self.code_verifier,
                "redirect_uri": self.redirect_uri,
            },
            code=code,
        )
        _check_success(response, document)
        granted = required_string(document, "scope", SCHEME)
        if QUOTA_SCOPE not in granted.split():
            raise ModelAuthenticationError(SCHEME, "Google did not grant the AI Plan scope.")
        subject = await _verified_subject(
            self.http_client, required_string(document, "id_token", SCHEME), self.registration.client_id, self.nonce
        )
        return GoogleAIPlanCredentials(
            account_id=subject,
            client_id=self.registration.client_id,
            access_token=required_string(document, "access_token", SCHEME),
            refresh_token=required_string(document, "refresh_token", SCHEME),
            expires_at=_access_expiry(document),
            dpop_jwk=key,
            dpop_nonce=response.headers.get("DPoP-Nonce"),
        )


async def _google_oauth_request(
    client: httpx2.AsyncClient,
    method: str,
    url: str,
    *,
    data: Mapping[str, str] | None = None,
    proof: str | None = None,
) -> tuple[httpx2.Response, dict[str, object]]:
    headers = {"User-Agent": user_agent(), "Accept": "application/json"}
    if proof is not None:
        headers["DPoP"] = proof
    async with client.stream(method, url, data=data, headers=headers, auth=no_auth, follow_redirects=False) as response:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > _RESPONSE_BYTES:
                raise CredentialRefreshError(SCHEME, "The Google OAuth response exceeds its size limit.")
            body.extend(chunk)
        try:
            document = json.loads(body)
        except ValueError:
            document = None
        if not isinstance(document, dict):
            raise CredentialRefreshError(SCHEME, "The Google OAuth response is invalid.")
        return response, document


async def _verified_subject(http_client: httpx2.AsyncClient, token: str, audience: str, nonce: str) -> str:
    # The ID token is used once to bind the Host account. Its key source is fixed, not selected by the token.
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError
        response, document = await _google_oauth_request(http_client, "GET", JWKS_URL)
        if response.status_code != 200:
            raise ValueError
        keys = document.get("keys")
        if not isinstance(keys, list):
            raise ValueError
        key = next(item for item in keys if isinstance(item, dict) and item.get("kid") == header["kid"])
        claims = jwt.decode(
            token,
            jwt.PyJWK.from_dict(key, algorithm="RS256").key,
            algorithms=["RS256"],
            audience=audience,
            issuer=["https://accounts.google.com", "accounts.google.com"],
            options={"require": ["iss", "aud", "exp", "iat", "sub", "nonce"]},
            leeway=30,
        )
        subject = claims["sub"]
        if (
            not isinstance(subject, str)
            or not 0 < len(subject) <= 255
            or not subject.isascii()
            or claims["nonce"] != nonce
            or ("azp" in claims and claims["azp"] != audience)
            or (isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and claims.get("azp") != audience)
        ):
            raise ValueError
        return subject
    except (ValueError, jwt.PyJWTError, StopIteration, httpx2.HTTPError, CredentialRefreshError):
        raise ModelAuthenticationError(SCHEME, "Google returned an invalid or unverifiable account identity.") from None


async def _grant(
    registration: GoogleAIPlanClient,
    client: httpx2.AsyncClient,
    key: DPoPKey,
    parameters: Mapping[str, str],
    *,
    code: str | None = None,
    nonce: str | None = None,
    server_time: int | None = None,
) -> tuple[httpx2.Response, dict[str, object]]:
    proof = key.proof(
        jti=_sha256(code) if code is not None else secrets.token_urlsafe(24),
        iat=int(datetime.now(UTC).timestamp()) if server_time is None else server_time,
        nonce=nonce,
    )
    try:
        return await _google_oauth_request(
            client,
            "POST",
            TOKEN_URL,
            data={
                "client_id": registration.client_id,
                "client_secret": registration.client_secret.get_secret_value(),
                **parameters,
            },
            proof=proof,
        )
    except httpx2.HTTPError:
        raise CredentialRefreshError(SCHEME, "The Google token endpoint could not be reached.") from None


def _access_expiry(document: dict[str, object]) -> datetime:
    return expiry(
        datetime.now(UTC),
        positive_integer(document.get("expires_in", 3599), "expires_in", provider=SCHEME),
        provider=SCHEME,
    )


def _check_success(response: httpx2.Response, document: dict[str, object]) -> None:
    error = document.get("error")
    if response.status_code == 200 and not error:
        return
    if error == "invalid_grant":
        message = "The Google authorization grant is no longer valid. Sign in again."
    elif error in ("unauthorized_client", "invalid_client", "invalid_scope"):
        message = "Google rejected this OAuth client or scope. Check the approved registration."
    else:
        message = f"The Google token endpoint rejected the request (HTTP {response.status_code})."
    raise CredentialRefreshError(SCHEME, message)


def _date(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        return int(parsed.timestamp()) if parsed.tzinfo is not None else None
    except ValueError:
        return None


async def refresh_google_ai_plan(
    registration: GoogleAIPlanClient, http_client: httpx2.AsyncClient, current: GoogleAIPlanCredentials
) -> GoogleAIPlanCredentials:
    if current.client_id != registration.client_id:
        raise CredentialRefreshError(SCHEME, "The Google client registration changed. Sign in again.")
    nonce, server_time = current.dpop_nonce, None
    for attempt in range(3):
        response, document = await _grant(
            registration,
            http_client,
            current.dpop_jwk,
            {
                "grant_type": "refresh_token",
                "refresh_token": current.refresh_token,
            },
            nonce=nonce,
            server_time=server_time,
        )
        new_nonce = response.headers.get("DPoP-Nonce")
        if attempt < 2:
            if document.get("error") == "use_dpop_nonce" and new_nonce and new_nonce != nonce:
                nonce = new_nonce
                continue
            if document.get("error") == "invalid_dpop_proof" and server_time is None:
                server_time = _date(response.headers.get("Date"))
                if server_time is not None:
                    continue
        _check_success(response, document)
        replacement = document.get("refresh_token", current.refresh_token)
        if not isinstance(replacement, str) or not replacement:
            raise CredentialRefreshError(SCHEME, "Google returned an invalid refresh token.")
        try:
            return GoogleAIPlanCredentials(
                account_id=current.account_id,
                client_id=current.client_id,
                access_token=required_string(document, "access_token", SCHEME),
                refresh_token=replacement,
                expires_at=_access_expiry(document),
                dpop_jwk=current.dpop_jwk,
                dpop_nonce=new_nonce or nonce,
            )
        except ValidationError:
            raise CredentialRefreshError(SCHEME, "Google returned an invalid refresh token.") from None
    raise AssertionError("The final DPoP attempt must return or fail")
