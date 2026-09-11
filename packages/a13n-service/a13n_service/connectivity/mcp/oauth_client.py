"""Standards-based OAuth discovery and token operations for Remote MCP."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx2
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.oauth2 import OAuth2Client
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata
from pydantic import ValidationError

from a13n_service.connectivity.bounds import MAX_REDIRECTS
from a13n_service.connectivity.http import (
    BoundedHttpResponse,
    ConnectivityHttpError,
    cookie_free_bounded_request,
)
from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError

from .domain import MCP_PROTOCOL_REVISION, MCPClientMetadata, MCPOAuthClientInput, OAuthTokenAuthMethod
from .oauth_http import OAuthTransport

_BEARER_PARAMETER = re.compile(r'(?P<name>[A-Za-z_][A-Za-z0-9_-]*)=(?:"(?P<quoted>[^"\\]*)"|(?P<token>[^,\s]+))')


class MCPOAuthError(ValueError):
    def __init__(self, code: str, *, action_required: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.action_required = action_required


@dataclass(frozen=True, slots=True)
class OAuthPreparation:
    redirect_uri: str
    resource_url: str
    issuer_url: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    client_id: str
    client_secret: str | None
    token_endpoint_auth_method: str
    registration_access_token: str | None
    registration_client_uri: str | None
    scope: str | None


@dataclass(frozen=True, slots=True)
class OAuthDiscovery:
    resource_url: str
    issuer_url: str
    authorization_endpoint: str
    token_endpoint: str
    scope: str | None
    metadata: dict[str, Any]
    token_auth_methods: tuple[OAuthTokenAuthMethod, ...]


def _metadata_issuer_matches(expected: str, actual: object) -> bool:
    if actual == expected:
        return True
    parsed = urlsplit(expected)
    if parsed.path not in {"", "/"}:
        return False
    # Root URLs share one metadata location; keep its declared spelling for callbacks.
    alternate = urlunsplit(parsed._replace(path="/" if not parsed.path else ""))
    return actual == alternate


def issuer_key(issuer: str) -> str:
    return hashlib.sha256(issuer.encode()).hexdigest()


def oauth_redirect_uri(public_origin: str, key: str) -> str:
    return f"{public_origin}/api/v1/oauth/mcp/callback/{key}"


def oauth_client_metadata(public_origin: str, key: str, client_name: str) -> MCPClientMetadata:
    return MCPClientMetadata(
        client_id=f"{public_origin}/api/v1/oauth/mcp/client-metadata/{key}.json",
        client_name=client_name,
        redirect_uris=(oauth_redirect_uri(public_origin, key),),
    )


@dataclass(frozen=True, slots=True)
class _ClientRegistration:
    endpoint: str | None
    client_id: str
    client_secret: str | None
    token_auth_method: str
    access_token: str | None
    management_uri: str | None


class MCPOAuthClient:
    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        endpoint_policy: EndpointPolicy,
        *,
        response_max_bytes: int = 1024 * 1024,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        self._http = http_client
        self._policy = endpoint_policy
        self._response_max_bytes = response_max_bytes
        self._max_redirects = min(max_redirects, MAX_REDIRECTS)

    async def prepare(
        self,
        endpoint_url: str,
        *,
        public_origin: str,
        client_name: str,
        client: MCPOAuthClientInput | None = None,
    ) -> OAuthPreparation:
        discovered = await self.discover(endpoint_url)
        metadata = discovered.metadata
        key = issuer_key(discovered.issuer_url)
        identity = oauth_client_metadata(public_origin, key, client_name)
        redirect_uri = identity.redirect_uris[0]
        if client is not None and client.issuer_url != discovered.issuer_url:
            raise MCPOAuthError("configured_issuer_mismatch")
        registration = await self._client_registration(
            metadata,
            client=client,
            supported_auth_methods=discovered.token_auth_methods,
            client_metadata_url=identity.client_id,
            redirect_uri=redirect_uri,
            client_name=client_name,
        )
        return OAuthPreparation(
            redirect_uri=redirect_uri,
            resource_url=discovered.resource_url,
            issuer_url=discovered.issuer_url,
            authorization_endpoint=discovered.authorization_endpoint,
            token_endpoint=discovered.token_endpoint,
            registration_endpoint=registration.endpoint,
            client_id=registration.client_id,
            client_secret=registration.client_secret,
            token_endpoint_auth_method=registration.token_auth_method,
            registration_access_token=registration.access_token,
            registration_client_uri=registration.management_uri,
            scope=discovered.scope,
        )

    async def discover(self, endpoint_url: str) -> OAuthDiscovery:
        endpoint = await self._validate(endpoint_url)
        challenge = await self._challenge(endpoint)
        resource_metadata = await self._resource_metadata(endpoint, challenge.get("resource_metadata"))
        try:
            ProtectedResourceMetadata.model_validate(resource_metadata)
        except ValidationError as error:
            raise MCPOAuthError("invalid_resource_metadata") from error
        issuers = resource_metadata.get("authorization_servers")
        if not isinstance(issuers, list) or not issuers or not isinstance(issuers[0], str):
            raise MCPOAuthError("authorization_server_missing")
        issuer = await self._validate(issuers[0])
        metadata = await self._authorization_metadata(issuer)
        try:
            OAuthMetadata.model_validate(metadata)
            for key in ("authorization_response_iss_parameter_supported", "client_id_metadata_document_supported"):
                if key in metadata and not isinstance(metadata[key], bool):
                    raise MCPOAuthError("invalid_authorization_metadata")
            if "token_endpoint_auth_methods_supported" in metadata and not isinstance(
                metadata["token_endpoint_auth_methods_supported"], list
            ):
                raise MCPOAuthError("invalid_authorization_metadata")
        except ValidationError as error:
            raise MCPOAuthError("invalid_authorization_metadata") from error
        authorization_endpoint = await self._metadata_endpoint(metadata, "authorization_endpoint")
        token_endpoint = await self._metadata_endpoint(metadata, "token_endpoint")
        if authorization_endpoint is None or token_endpoint is None:
            raise MCPOAuthError("invalid_authorization_metadata")
        methods = metadata.get("code_challenge_methods_supported")
        if not isinstance(methods, list) or "S256" not in methods:
            raise MCPOAuthError("pkce_s256_required")
        scope = challenge.get("scope")
        if scope is None:
            scopes = resource_metadata.get("scopes_supported")
            if isinstance(scopes, list) and len(scopes) <= 128 and all(isinstance(item, str) for item in scopes):
                scope = " ".join(scopes) or None
        scope = _safe_scope(scope)
        return OAuthDiscovery(
            resource_url=resource_metadata["resource"],
            issuer_url=metadata["issuer"],
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            scope=scope,
            metadata=metadata,
            token_auth_methods=_supported_token_auth_methods(metadata),
        )

    async def _client_registration(
        self,
        metadata: dict[str, Any],
        *,
        client_metadata_url: str,
        redirect_uri: str,
        client_name: str,
        client: MCPOAuthClientInput | None,
        supported_auth_methods: tuple[OAuthTokenAuthMethod, ...],
    ) -> _ClientRegistration:
        if client is not None:
            if client.token_endpoint_auth_method not in supported_auth_methods:
                raise MCPOAuthError("unsupported_token_endpoint_auth_method")
            secret = client.client_secret.get_secret_value() if client.client_secret is not None else None
            return _ClientRegistration(None, client.client_id, secret, client.token_endpoint_auth_method, None, None)
        if metadata.get("client_id_metadata_document_supported") is True and "none" in supported_auth_methods:
            return _ClientRegistration(None, client_metadata_url, None, "none", None, None)
        endpoint = await self._metadata_endpoint(metadata, "registration_endpoint", required=False)
        if endpoint is None:
            raise MCPOAuthError("client_registration_unsupported")
        if not supported_auth_methods:
            raise MCPOAuthError("unsupported_token_endpoint_auth_method")
        response = await self._post_json(
            endpoint,
            {
                "client_name": client_name,
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": supported_auth_methods[0],
            },
        )
        try:
            registration = _client_registration(response, endpoint=endpoint)
            if registration.token_auth_method not in supported_auth_methods:
                raise MCPOAuthError("unsupported_token_endpoint_auth_method")
            return registration
        except MCPOAuthError:
            try:
                await self.cleanup_registration_bundle(response)
            except MCPOAuthError:
                pass
            raise

    async def exchange_code(
        self,
        preparation: OAuthPreparation,
        *,
        code: str,
        verifier: str,
    ) -> dict[str, Any]:
        client = self._token_client(
            preparation.client_id,
            preparation.client_secret,
            preparation.token_endpoint_auth_method,
            redirect_uri=preparation.redirect_uri,
        )
        try:
            # Authlib's dynamic httpx2 import hides its AsyncClient base from Pyright.
            async with cast(httpx2.AsyncClient, client):
                token = await client.fetch_token(
                    preparation.token_endpoint,
                    grant_type="authorization_code",
                    code=code,
                    code_verifier=verifier,
                    resource=preparation.resource_url,
                )
        except (OAuthError, ConnectivityHttpError, EndpointPolicyError, ValueError, TypeError) as error:
            raise _token_error(error) from error
        return _credential_bundle(preparation, token)

    async def refresh(self, bundle: dict[str, Any]) -> dict[str, Any]:
        refresh_token = bundle.get("refresh_token")
        token_endpoint = bundle.get("token_endpoint")
        client_id = bundle.get("client_id")
        resource = bundle.get("resource")
        if not all(isinstance(value, str) and value for value in (refresh_token, token_endpoint, client_id, resource)):
            raise MCPOAuthError("reauthorization_required", action_required=True)
        client_secret = bundle.get("client_secret")
        auth_method = bundle.get("token_endpoint_auth_method")
        if not isinstance(auth_method, str):
            raise MCPOAuthError("reauthorization_required", action_required=True)
        assert isinstance(client_id, str)
        client = self._token_client(client_id, client_secret, auth_method)
        try:
            async with cast(httpx2.AsyncClient, client):
                token = await client.refresh_token(token_endpoint, refresh_token=refresh_token, resource=resource)
        except (OAuthError, ConnectivityHttpError, EndpointPolicyError, ValueError, TypeError) as error:
            raise _token_error(error) from error
        _validate_token(token)
        merged = dict(bundle)
        merged.update(token)
        return merged

    def _token_client(
        self,
        client_id: str,
        client_secret: str | None,
        auth_method: str,
        *,
        redirect_uri: str | None = None,
    ) -> AsyncOAuth2Client:
        if auth_method not in {"none", "client_secret_basic", "client_secret_post"}:
            raise MCPOAuthError("unsupported_token_endpoint_auth_method")
        if client_secret is not None and not isinstance(client_secret, str):
            raise MCPOAuthError("invalid_client_registration")
        if auth_method != "none" and not client_secret:
            raise MCPOAuthError("invalid_client_registration")
        return AsyncOAuth2Client(
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint_auth_method=auth_method,
            redirect_uri=redirect_uri,
            code_challenge_method="S256",
            transport=OAuthTransport(self._http, self._policy, max_bytes=self._response_max_bytes),
            follow_redirects=False,
        )

    async def cleanup_registration_bundle(self, bundle: dict[str, Any]) -> bool:
        registration_uri = bundle.get("registration_client_uri")
        registration_token = bundle.get("registration_access_token")
        if registration_uri is None and registration_token is None:
            return True
        if not isinstance(registration_uri, str) or not isinstance(registration_token, str):
            return False
        endpoint = await self._validate(registration_uri)
        try:
            response = await self._request(
                "DELETE",
                endpoint,
                headers={"Authorization": f"Bearer {registration_token}"},
                sensitive=True,
            )
        except httpx2.HTTPError:
            return False
        return response.status_code in {200, 204, 404}

    async def _challenge(self, endpoint: str) -> dict[str, str]:
        await self._validate(endpoint)
        response = await self._request(
            "POST",
            endpoint,
            headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_REVISION,
                    "capabilities": {},
                    "clientInfo": {"name": "a13n-service", "version": "1"},
                },
            },
            sensitive=False,
        )
        if response.status_code != 401:
            return {}
        value = response.headers.get("www-authenticate")
        if value is None:
            return {}
        if (
            len(value.encode()) > 8192
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or not value.lower().startswith("bearer")
        ):
            raise MCPOAuthError("invalid_bearer_challenge")
        parameters = {
            match.group("name").lower(): match.group("quoted") or match.group("token")
            for match in _BEARER_PARAMETER.finditer(value[6:])
        }
        if "resource_metadata" in value.lower() and "resource_metadata" not in parameters:
            raise MCPOAuthError("invalid_bearer_challenge")
        return parameters

    async def _resource_metadata(self, endpoint: str, advertised: str | None) -> dict[str, Any]:
        candidates = [(advertised, endpoint)] if advertised is not None else _resource_metadata_locations(endpoint)
        mismatch: MCPOAuthError | None = None
        for candidate, expected_resource in candidates:
            try:
                metadata = await self._get_json(candidate)
                resource = metadata.get("resource")
                if resource != expected_resource and not _metadata_resource_covers_endpoint(
                    endpoint,
                    candidate,
                    resource,
                ):
                    raise MCPOAuthError("resource_mismatch")
                return metadata
            except MCPOAuthError as error:
                if advertised is not None or error.code not in {"metadata_not_found", "resource_mismatch"}:
                    raise
                if error.code == "resource_mismatch":
                    mismatch = error
        if mismatch is not None:
            raise mismatch
        raise MCPOAuthError("protected_resource_metadata_missing")

    async def _authorization_metadata(self, issuer: str) -> dict[str, Any]:
        parsed = urlsplit(issuer)
        base = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        if parsed.query:
            raise MCPOAuthError("invalid_issuer_identifier")
        path = parsed.path.rstrip("/")
        candidates = (
            f"{base}/.well-known/oauth-authorization-server{path}",
            f"{base}/.well-known/openid-configuration{path}",
            f"{issuer.rstrip('/')}/.well-known/openid-configuration",
        )
        for candidate in dict.fromkeys(candidates):
            try:
                metadata = await self._get_json(candidate)
            except MCPOAuthError as error:
                if error.code == "metadata_not_found":
                    continue
                raise
            if not _metadata_issuer_matches(issuer, metadata.get("issuer")):
                raise MCPOAuthError("issuer_mismatch")
            return metadata
        raise MCPOAuthError("authorization_metadata_missing")

    async def _metadata_endpoint(
        self,
        metadata: dict[str, Any],
        key: str,
        *,
        required: bool = True,
    ) -> str | None:
        value = metadata.get(key)
        if value is None and not required:
            return None
        if not isinstance(value, str):
            raise MCPOAuthError("invalid_authorization_metadata")
        return await self._validate(value)

    async def _get_json(self, endpoint: str) -> dict[str, Any]:
        target = await self._validate(endpoint)
        response = await self._request(
            "GET",
            target,
            headers={"Accept": "application/json"},
            sensitive=False,
        )
        if response.status_code == 404:
            raise MCPOAuthError("metadata_not_found")
        if response.status_code != 200:
            raise MCPOAuthError("metadata_unavailable")
        return await self._json_body(response)

    async def _post_json(self, endpoint: str, value: dict[str, Any]) -> dict[str, Any]:
        target = await self._validate(endpoint)
        response = await self._request(
            "POST",
            target,
            headers={"Accept": "application/json"},
            json=value,
            sensitive=True,
        )
        if response.status_code not in {200, 201}:
            raise MCPOAuthError("client_registration_failed")
        return await self._json_body(response)

    async def _json_body(self, response: BoundedHttpResponse) -> dict[str, Any]:
        if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise MCPOAuthError("invalid_oauth_response")
        try:
            value: Any = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
            raise MCPOAuthError("invalid_oauth_response") from error
        if not isinstance(value, dict):
            raise MCPOAuthError("invalid_oauth_response")
        return value

    async def _validate(self, endpoint: str) -> str:
        try:
            await self._policy.validate(endpoint, resolve_dns=True)
            return endpoint
        except EndpointPolicyError as error:
            raise MCPOAuthError("unsafe_oauth_endpoint") from error

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        headers: dict[str, str],
        sensitive: bool,
        json: dict[str, Any] | None = None,
        content: bytes | None = None,
    ) -> BoundedHttpResponse:
        current = endpoint
        for redirect_count in range(self._max_redirects + 1):
            current = await self._validate(current)
            try:
                response = await cookie_free_bounded_request(
                    self._http,
                    method,
                    current,
                    headers=headers,
                    max_bytes=self._response_max_bytes,
                    json_body=json,
                    content=content,
                )
            except ConnectivityHttpError as error:
                raise MCPOAuthError(error.code) from error
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            if redirect_count == self._max_redirects:
                raise MCPOAuthError("too_many_redirects")
            location = response.headers.get("location")
            if location is None:
                raise MCPOAuthError("invalid_redirect")
            try:
                current, same_origin = await self._policy.validate_redirect(
                    current,
                    urljoin(current, location),
                    resolve_dns=True,
                )
            except EndpointPolicyError as error:
                raise MCPOAuthError("unsafe_oauth_endpoint") from error
            if sensitive and not same_origin:
                raise MCPOAuthError("credential_origin_redirect")
            if method == "GET":
                continue
            if response.status_code not in {307, 308}:
                raise MCPOAuthError("unsafe_oauth_redirect")
        raise MCPOAuthError("too_many_redirects")


def authorization_url(
    preparation: OAuthPreparation,
    *,
    state: str,
    code_verifier: str,
) -> str:
    client = OAuth2Client(
        session=None,
        client_id=preparation.client_id,
        redirect_uri=preparation.redirect_uri,
        scope=preparation.scope,
        code_challenge_method="S256",
    )
    url, _ = client.create_authorization_url(
        preparation.authorization_endpoint,
        state=state,
        code_verifier=code_verifier,
        resource=preparation.resource_url,
    )
    return url


def _token_error(error: Exception) -> MCPOAuthError:
    if isinstance(error, ConnectivityHttpError):
        return MCPOAuthError(error.code)
    if isinstance(error, EndpointPolicyError):
        return MCPOAuthError("unsafe_oauth_endpoint")
    if isinstance(error, OAuthError):
        action_required = error.error in {"invalid_grant", "invalid_token", "insufficient_scope"}
        return MCPOAuthError(
            "reauthorization_required" if action_required else "token_exchange_failed",
            action_required=action_required,
        )
    return MCPOAuthError("invalid_oauth_response")


def _resource_metadata_locations(endpoint: str) -> tuple[tuple[str, str], ...]:
    parsed = urlsplit(endpoint)
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    path = parsed.path.rstrip("/")
    path_location = urlunsplit(
        (parsed.scheme, parsed.netloc, f"/.well-known/oauth-protected-resource{path}", parsed.query, "")
    )
    root_location = f"{origin}/.well-known/oauth-protected-resource"
    locations = ((path_location, endpoint), (root_location, origin))
    return locations[:1] if path_location == root_location else locations


def _metadata_resource_covers_endpoint(endpoint: str, metadata_url: str, resource: object) -> bool:
    if not isinstance(resource, str):
        return False
    endpoint_parts = urlsplit(endpoint)
    resource_parts = urlsplit(resource)
    if resource_parts.username is not None or resource_parts.password is not None or resource_parts.fragment:
        return False
    try:
        resource_port = resource_parts.port
    except ValueError:
        return False
    if (
        endpoint_parts.scheme.lower(),
        endpoint_parts.hostname,
        endpoint_parts.port,
    ) != (
        resource_parts.scheme.lower(),
        resource_parts.hostname,
        resource_port,
    ):
        return False
    resource_path = resource_parts.path.rstrip("/")
    endpoint_path = endpoint_parts.path.rstrip("/")
    if resource_path and endpoint_path != resource_path and not endpoint_path.startswith(f"{resource_path}/"):
        return False
    if resource_parts.query and resource_parts.query != endpoint_parts.query:
        return False
    return any(location == metadata_url for location, _ in _resource_metadata_locations(resource))


def _supported_token_auth_methods(metadata: dict[str, Any]) -> tuple[OAuthTokenAuthMethod, ...]:
    supported = metadata.get("token_endpoint_auth_methods_supported", ["client_secret_basic"])
    methods: tuple[OAuthTokenAuthMethod, ...] = ("none", "client_secret_basic", "client_secret_post")
    return tuple(method for method in methods if method in supported)


def _optional_string(value: dict[str, Any], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item:
        raise MCPOAuthError("invalid_client_registration")
    return item


def _client_registration(value: dict[str, Any], *, endpoint: str) -> _ClientRegistration:
    client_id = value.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise MCPOAuthError("invalid_client_registration")
    token_auth_method = value.get("token_endpoint_auth_method", "client_secret_basic")
    if not isinstance(token_auth_method, str) or token_auth_method not in {
        "none",
        "client_secret_basic",
        "client_secret_post",
    }:
        raise MCPOAuthError("unsupported_token_endpoint_auth_method")
    client_secret = _optional_string(value, "client_secret")
    if token_auth_method != "none" and client_secret is None:
        raise MCPOAuthError("invalid_client_registration")
    return _ClientRegistration(
        endpoint=endpoint,
        client_id=client_id,
        client_secret=client_secret,
        token_auth_method=token_auth_method,
        access_token=_optional_string(value, "registration_access_token"),
        management_uri=_optional_string(value, "registration_client_uri"),
    )


def _safe_scope(value: str | None) -> str | None:
    if value is None:
        return None
    tokens = value.split(" ")
    if (
        not value
        or len(value.encode()) > 2048
        or any(not token for token in tokens)
        or any(ord(character) < 0x21 or ord(character) > 0x7E for token in tokens for character in token)
    ):
        raise MCPOAuthError("invalid_oauth_scope")
    return value


def _credential_bundle(preparation: OAuthPreparation, token: dict[str, Any]) -> dict[str, Any]:
    _validate_token(token)
    bundle = {
        "kind": "oauth",
        "access_token": token["access_token"],
        "token_type": "Bearer",
        "refresh_token": _optional_string(token, "refresh_token"),
        "scope": _optional_string(token, "scope") or preparation.scope,
        "expires_in": token.get("expires_in"),
        "issuer": preparation.issuer_url,
        "resource": preparation.resource_url,
        "token_endpoint": preparation.token_endpoint,
        "client_id": preparation.client_id,
        "client_secret": preparation.client_secret,
        "token_endpoint_auth_method": preparation.token_endpoint_auth_method,
        "registration_access_token": preparation.registration_access_token,
        "registration_client_uri": preparation.registration_client_uri,
    }
    return bundle


def _validate_token(token: dict[str, Any]) -> None:
    access_token = token.get("access_token")
    token_type = token.get("token_type")
    if (
        not isinstance(access_token, str)
        or not access_token
        or not isinstance(token_type, str)
        or token_type.lower() != "bearer"
    ):
        raise MCPOAuthError("invalid_token_response")
