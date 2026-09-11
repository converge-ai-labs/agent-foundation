"""Standards-based OAuth discovery and token operations for Remote MCP."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal, cast
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import httpx2
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.oauth2 import OAuth2Client
from mcp.client.auth.exceptions import OAuthFlowError, OAuthRegistrationError, OAuthTokenError
from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider
from mcp.client.auth.oauth2 import TokenStorage, check_registration_usable
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_client_info_from_metadata_url,
    create_client_registration_request,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
    get_client_metadata_scopes,
    handle_registration_response,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from pydantic import ValidationError

from a13n_service.connectivity.bounds import MAX_REDIRECTS
from a13n_service.connectivity.http import (
    BoundedHttpResponse,
    ConnectivityHttpError,
    cookie_free_bounded_request,
)
from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError

from .domain import MCP_PROTOCOL_REVISION, MCPClientMetadata, MCPOAuthClientInput, OAuthGrantType, OAuthTokenAuthMethod
from .oauth_http import OAuthTransport

_ACTION_REQUIRED_TOKEN_ERRORS = frozenset(
    {"invalid_client", "invalid_grant", "invalid_scope", "invalid_token", "insufficient_scope", "unauthorized_client"}
)


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
    client_secret: str | None = field(repr=False)
    token_endpoint_auth_method: str
    registration_access_token: str | None
    registration_client_uri: str | None
    scope: str | None


@dataclass(frozen=True, slots=True)
class OAuthDiscovery:
    resource_url: str
    issuer_url: str
    authorization_endpoint: str | None
    token_endpoint: str
    scope: str | None
    metadata: OAuthMetadata
    resource_metadata: ProtectedResourceMetadata
    token_auth_methods: tuple[OAuthTokenAuthMethod, ...]
    grant_types: tuple[OAuthGrantType, ...]
    client_registration: Literal["metadata_document", "dynamic", "manual"]


@dataclass(frozen=True, slots=True)
class OAuthClientContext:
    resource_url: str
    issuer_url: str
    token_endpoint: str
    client_id: str
    client_secret: str | None = field(repr=False)
    token_endpoint_auth_method: OAuthTokenAuthMethod
    scope: str | None
    grant_type: Literal["authorization_code", "client_credentials"]


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
    client_secret: str | None = field(repr=False)
    token_auth_method: str
    access_token: str | None
    management_uri: str | None


class _MemoryTokenStorage(TokenStorage):
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


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
        if discovered.authorization_endpoint is None:
            raise MCPOAuthError("authorization_code_unsupported")
        if (
            discovered.metadata.code_challenge_methods_supported is None
            or "S256" not in discovered.metadata.code_challenge_methods_supported
        ):
            raise MCPOAuthError("pkce_s256_required")
        if "authorization_code" not in discovered.grant_types:
            raise MCPOAuthError("authorization_code_unsupported")
        metadata = discovered.metadata
        key = issuer_key(discovered.issuer_url)
        identity = oauth_client_metadata(public_origin, key, client_name)
        redirect_uri = identity.redirect_uris[0]
        if client is not None and client.issuer_url != discovered.issuer_url:
            raise MCPOAuthError("configured_issuer_mismatch")
        registration = await self._client_registration(
            metadata,
            strategy=discovered.client_registration,
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
        advertised_metadata, challenged_scope = await self._challenge(endpoint)
        resource_metadata = await self._resource_metadata(endpoint, advertised_metadata)
        issuer = await self._validate(str(resource_metadata.authorization_servers[0]))
        metadata = await self._authorization_metadata(issuer)
        token_endpoint = await self._metadata_endpoint(metadata, "token_endpoint")
        if token_endpoint is None:
            raise MCPOAuthError("invalid_authorization_metadata")
        advertised_grants = tuple(metadata.grant_types_supported or ("authorization_code", "refresh_token"))
        authorization_endpoint = await self._metadata_endpoint(metadata, "authorization_endpoint", required=False)
        methods = _supported_token_auth_methods(metadata)
        usable_grants: list[OAuthGrantType] = []
        if (
            "authorization_code" in advertised_grants
            and authorization_endpoint is not None
            and metadata.code_challenge_methods_supported is not None
            and "S256" in metadata.code_challenge_methods_supported
        ):
            usable_grants.append("authorization_code")
        if "client_credentials" in advertised_grants and any(method != "none" for method in methods):
            usable_grants.append("client_credentials")
        scope = _safe_scope(
            get_client_metadata_scopes(
                challenged_scope,
                resource_metadata,
                metadata,
                list(advertised_grants),
            )
        )
        return OAuthDiscovery(
            resource_url=str(resource_metadata.resource),
            issuer_url=str(metadata.issuer),
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            scope=scope,
            metadata=metadata,
            resource_metadata=resource_metadata,
            token_auth_methods=methods,
            grant_types=tuple(usable_grants),
            client_registration=_automatic_registration(metadata, methods),
        )

    async def _client_registration(
        self,
        metadata: OAuthMetadata,
        *,
        strategy: Literal["metadata_document", "dynamic", "manual"],
        client_metadata_url: str,
        redirect_uri: str,
        client_name: str,
        client: MCPOAuthClientInput | None,
        supported_auth_methods: tuple[OAuthTokenAuthMethod, ...],
    ) -> _ClientRegistration:
        if client is not None:
            return _configured_registration(client, supported_auth_methods)
        if strategy == "metadata_document":
            info = create_client_info_from_metadata_url(client_metadata_url)
            return _registration(info, endpoint=None)
        if strategy == "manual":
            raise MCPOAuthError("client_registration_unsupported")
        endpoint = await self._metadata_endpoint(metadata, "registration_endpoint", required=False)
        if endpoint is None or not supported_auth_methods:
            raise MCPOAuthError("client_registration_unsupported")
        request = create_client_registration_request(
            metadata,
            OAuthClientMetadata.model_validate(
                {
                    "client_name": client_name,
                    "redirect_uris": [redirect_uri],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": supported_auth_methods[0],
                }
            ),
            str(metadata.issuer),
        )
        response = await self._send_sdk_request(request, sensitive=True)
        if response.status_code not in {200, 201}:
            raise MCPOAuthError("client_registration_failed")
        try:
            info = await handle_registration_response(response)
            check_registration_usable(info)
            registration = _registration(info, endpoint=endpoint, wire=_registration_cleanup_bundle(response))
            if registration.token_auth_method not in supported_auth_methods:
                raise MCPOAuthError("unsupported_token_endpoint_auth_method")
            return registration
        except (MCPOAuthError, OAuthRegistrationError, ValueError) as error:
            try:
                await self.cleanup_registration_bundle(_registration_cleanup_bundle(response))
            except MCPOAuthError:
                pass
            if isinstance(error, MCPOAuthError):
                raise
            raise MCPOAuthError("invalid_client_registration") from error

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

    async def refresh(self, bundle: dict[str, Any], client: OAuthClientContext) -> dict[str, Any]:
        if client.grant_type == "client_credentials":
            if client.client_secret is None:
                raise MCPOAuthError("reauthorization_required", action_required=True)
            return await self.acquire_client_credentials(
                endpoint_url=client.resource_url,
                issuer_url=client.issuer_url,
                client_id=client.client_id,
                client_secret=client.client_secret,
                token_endpoint_auth_method=client.token_endpoint_auth_method,
                scope=client.scope,
            )
        refresh_token = bundle.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise MCPOAuthError("reauthorization_required", action_required=True)
        token_client = self._token_client(
            client.client_id,
            client.client_secret,
            client.token_endpoint_auth_method,
        )
        try:
            async with cast(httpx2.AsyncClient, token_client):
                token = await token_client.refresh_token(
                    client.token_endpoint,
                    refresh_token=refresh_token,
                    resource=client.resource_url,
                )
        except (OAuthError, ConnectivityHttpError, EndpointPolicyError, ValueError, TypeError) as error:
            raise _token_error(error) from error
        _validate_token(token)
        merged = dict(bundle)
        merged.update(token)
        return merged

    async def acquire_client_credentials(
        self,
        *,
        endpoint_url: str,
        issuer_url: str,
        client_id: str,
        client_secret: str,
        token_endpoint_auth_method: str,
        scope: str | None,
    ) -> dict[str, Any]:
        """Acquire a machine token through the SDK provider under Service network policy."""
        endpoint = await self._validate(endpoint_url)
        storage = _MemoryTokenStorage()
        provider = ClientCredentialsOAuthProvider(
            server_url=endpoint,
            storage=storage,
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint_auth_method=_confidential_auth_method(token_endpoint_auth_method),
            scope=scope,
            issuer=issuer_url,
        )
        request = httpx2.Request(
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
        )
        flow = provider.async_auth_flow(request)
        try:
            outbound = await anext(flow)
            for _ in range(16):
                request_body = await outbound.aread()
                response = await self._send_sdk_request(outbound, sensitive=True)
                if _client_credentials_rejected(request_body, response):
                    raise MCPOAuthError("reauthorization_required", action_required=True)
                try:
                    outbound = await flow.asend(response)
                except StopAsyncIteration:
                    break
            else:
                raise MCPOAuthError("oauth_flow_too_many_requests")
        except MCPOAuthError:
            raise
        except (OAuthFlowError, OAuthRegistrationError, OAuthTokenError, ValidationError, ValueError) as error:
            raise MCPOAuthError("token_exchange_failed") from error
        token = storage.tokens
        if token is None:
            raise MCPOAuthError("invalid_token_response")
        return {
            "kind": "oauth",
            "grant_type": "client_credentials",
            "access_token": token.access_token,
            "token_type": token.token_type,
            "refresh_token": token.refresh_token,
            "scope": token.scope or scope,
            "expires_in": token.expires_in,
        }

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

    async def _challenge(self, endpoint: str) -> tuple[str | None, str | None]:
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
            return None, None
        value = response.headers.get("www-authenticate")
        if value is None:
            return None, None
        if (
            len(value.encode()) > 8192
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or not value.lower().startswith("bearer")
        ):
            raise MCPOAuthError("invalid_bearer_challenge")
        sdk_response = httpx2.Response(401, headers={"WWW-Authenticate": value})
        advertised = extract_resource_metadata_from_www_auth(sdk_response)
        if "resource_metadata" in value.lower() and advertised is None:
            raise MCPOAuthError("invalid_bearer_challenge")
        return advertised, extract_scope_from_www_auth(sdk_response)

    async def _resource_metadata(self, endpoint: str, advertised: str | None) -> ProtectedResourceMetadata:
        candidates = build_protected_resource_metadata_discovery_urls(advertised, endpoint.rstrip("/"))
        mismatch: MCPOAuthError | None = None
        for candidate in candidates:
            try:
                value = await self._get_json(candidate)
                resource = value.get("resource")
                if isinstance(resource, str) and not _resource_matches_candidate(resource, endpoint, candidate):
                    raise MCPOAuthError("resource_mismatch")
                metadata = ProtectedResourceMetadata.model_validate(value)
                return metadata
            except ValidationError as error:
                raise MCPOAuthError("invalid_resource_metadata") from error
            except MCPOAuthError as error:
                if advertised is not None or error.code not in {"metadata_not_found", "resource_mismatch"}:
                    raise
                if error.code == "resource_mismatch":
                    mismatch = error
        if mismatch is not None:
            raise mismatch
        raise MCPOAuthError("protected_resource_metadata_missing")

    async def _authorization_metadata(self, issuer: str) -> OAuthMetadata:
        if urlsplit(issuer).query:
            raise MCPOAuthError("invalid_issuer_identifier")
        for candidate in build_oauth_authorization_server_metadata_discovery_urls(issuer, issuer):
            try:
                value = await self._get_json(candidate)
                if not _metadata_issuer_matches(issuer, value.get("issuer")):
                    raise MCPOAuthError("issuer_mismatch")
                metadata = OAuthMetadata.model_validate(value)
            except ValidationError as error:
                raise MCPOAuthError("invalid_authorization_metadata") from error
            except MCPOAuthError as error:
                if error.code == "metadata_not_found":
                    continue
                raise
            return metadata
        raise MCPOAuthError("authorization_metadata_missing")

    async def _metadata_endpoint(
        self,
        metadata: OAuthMetadata,
        key: str,
        *,
        required: bool = True,
    ) -> str | None:
        value = getattr(metadata, key)
        if value is None and not required:
            return None
        if value is None:
            raise MCPOAuthError("invalid_authorization_metadata")
        return await self._validate(str(value))

    async def _send_sdk_request(self, request: httpx2.Request, *, sensitive: bool) -> httpx2.Response:
        response = await self._request(
            request.method,
            str(request.url),
            headers=dict(request.headers),
            content=await request.aread(),
            sensitive=sensitive,
        )
        return httpx2.Response(
            response.status_code,
            headers=response.headers,
            content=response.body,
            request=request,
        )

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


def _resource_matches_candidate(resource: str, endpoint: str, metadata_url: str) -> bool:
    if resource == endpoint:
        return True
    try:
        endpoint_parts = urlsplit(endpoint)
        resource_parts = urlsplit(resource)
    except ValueError:
        return False
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
    locations = build_protected_resource_metadata_discovery_urls(None, resource.rstrip("/"))
    return metadata_url in locations


def _supported_token_auth_methods(metadata: OAuthMetadata) -> tuple[OAuthTokenAuthMethod, ...]:
    supported = metadata.token_endpoint_auth_methods_supported or ["client_secret_basic"]
    methods: tuple[OAuthTokenAuthMethod, ...] = ("none", "client_secret_basic", "client_secret_post")
    return tuple(method for method in methods if method in supported)


def _automatic_registration(
    metadata: OAuthMetadata,
    methods: tuple[OAuthTokenAuthMethod, ...],
) -> Literal["metadata_document", "dynamic", "manual"]:
    if "none" in methods and metadata.client_id_metadata_document_supported is True:
        return "metadata_document"
    if methods and metadata.registration_endpoint is not None:
        return "dynamic"
    return "manual"


def _optional_string(value: dict[str, Any], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item:
        raise MCPOAuthError("invalid_client_registration")
    return item


def _registration(
    info: OAuthClientInformationFull, *, endpoint: str | None, wire: dict[str, Any] | None = None
) -> _ClientRegistration:
    token_auth_method = info.token_endpoint_auth_method or "none"
    if token_auth_method not in {
        "none",
        "client_secret_basic",
        "client_secret_post",
    }:
        raise MCPOAuthError("unsupported_token_endpoint_auth_method")
    client_secret = info.client_secret
    if token_auth_method != "none" and client_secret is None:
        raise MCPOAuthError("invalid_client_registration")
    return _ClientRegistration(
        endpoint=endpoint,
        client_id=info.client_id,
        client_secret=client_secret,
        token_auth_method=token_auth_method,
        access_token=_optional_string(wire, "registration_access_token") if wire is not None else None,
        management_uri=_optional_string(wire, "registration_client_uri") if wire is not None else None,
    )


def _configured_registration(
    client: MCPOAuthClientInput,
    supported_auth_methods: tuple[OAuthTokenAuthMethod, ...],
) -> _ClientRegistration:
    if client.token_endpoint_auth_method not in supported_auth_methods:
        raise MCPOAuthError("unsupported_token_endpoint_auth_method")
    secret = client.client_secret.get_secret_value() if client.client_secret is not None else None
    return _ClientRegistration(None, client.client_id, secret, client.token_endpoint_auth_method, None, None)


def _registration_cleanup_bundle(response: httpx2.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _confidential_auth_method(value: object) -> Literal["client_secret_basic", "client_secret_post"]:
    if value == "client_secret_basic" or value == "client_secret_post":
        return cast(Literal["client_secret_basic", "client_secret_post"], value)
    raise MCPOAuthError("unsupported_token_endpoint_auth_method")


def _client_credentials_rejected(request_body: bytes, response: httpx2.Response) -> bool:
    try:
        form = parse_qs(request_body.decode("ascii"), strict_parsing=True)
    except (UnicodeDecodeError, ValueError):
        return False
    if form.get("grant_type") != ["client_credentials"]:
        return False
    if response.status_code in {401, 403}:
        return True
    if response.status_code != 400:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("error") in _ACTION_REQUIRED_TOKEN_ERRORS


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
        "grant_type": "authorization_code",
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
