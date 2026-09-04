"""Standards-based OAuth discovery and token operations for Remote MCP."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

import httpx2

from a13n_service.connectivity.bounds import MAX_REDIRECTS
from a13n_service.connectivity.http import (
    BoundedHttpResponse,
    ConnectivityHttpError,
    cookie_free_bounded_request,
)
from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError

from .domain import MCP_PROTOCOL_REVISION

_BEARER_PARAMETER = re.compile(r'(?P<name>[A-Za-z_][A-Za-z0-9_-]*)=(?:"(?P<quoted>[^"\\]*)"|(?P<token>[^,\s]+))')


class MCPOAuthError(ValueError):
    def __init__(self, code: str, *, action_required: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.action_required = action_required


@dataclass(frozen=True, slots=True)
class OAuthPreparation:
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
        client_metadata_url: str,
        redirect_uri: str,
        client_name: str,
    ) -> OAuthPreparation:
        endpoint = await self._validate(endpoint_url)
        challenge = await self._challenge(endpoint)
        resource_metadata = await self._resource_metadata(endpoint, challenge.get("resource_metadata"))
        if resource_metadata.get("resource") != endpoint:
            raise MCPOAuthError("resource_mismatch")
        issuers = resource_metadata.get("authorization_servers")
        if not isinstance(issuers, list) or not issuers or not isinstance(issuers[0], str):
            raise MCPOAuthError("authorization_server_missing")
        issuer = await self._validate(issuers[0])
        metadata = await self._authorization_metadata(issuer)
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
        registration = await self._client_registration(
            metadata,
            client_metadata_url=client_metadata_url,
            redirect_uri=redirect_uri,
            client_name=client_name,
        )
        return OAuthPreparation(
            resource_url=endpoint,
            issuer_url=issuer,
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            registration_endpoint=registration.endpoint,
            client_id=registration.client_id,
            client_secret=registration.client_secret,
            token_endpoint_auth_method=registration.token_auth_method,
            registration_access_token=registration.access_token,
            registration_client_uri=registration.management_uri,
            scope=scope,
        )

    async def _client_registration(
        self,
        metadata: dict[str, Any],
        *,
        client_metadata_url: str,
        redirect_uri: str,
        client_name: str,
    ) -> _ClientRegistration:
        if metadata.get("client_id_metadata_document_supported") is True:
            return _ClientRegistration(None, client_metadata_url, None, "none", None, None)
        endpoint = await self._metadata_endpoint(metadata, "registration_endpoint", required=False)
        if endpoint is None:
            raise MCPOAuthError("client_registration_unsupported")
        response = await self._post_json(
            endpoint,
            {
                "client_name": client_name,
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
        try:
            return _client_registration(response, endpoint=endpoint)
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
        redirect_uri: str,
    ) -> dict[str, Any]:
        values = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": preparation.client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "resource": preparation.resource_url,
        }
        token = await self._post_form(
            preparation.token_endpoint,
            values,
            client_id=preparation.client_id,
            client_secret=preparation.client_secret,
            auth_method=preparation.token_endpoint_auth_method,
        )
        return _credential_bundle(preparation, token)

    async def refresh(self, bundle: dict[str, Any]) -> dict[str, Any]:
        refresh_token = bundle.get("refresh_token")
        token_endpoint = bundle.get("token_endpoint")
        client_id = bundle.get("client_id")
        resource = bundle.get("resource")
        if not all(isinstance(value, str) and value for value in (refresh_token, token_endpoint, client_id, resource)):
            raise MCPOAuthError("reauthorization_required", action_required=True)
        assert isinstance(refresh_token, str)
        assert isinstance(token_endpoint, str)
        assert isinstance(client_id, str)
        assert isinstance(resource, str)
        values = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "resource": resource,
        }
        client_secret = bundle.get("client_secret")
        auth_method = bundle.get("token_endpoint_auth_method")
        if not isinstance(auth_method, str):
            raise MCPOAuthError("reauthorization_required", action_required=True)
        token = await self._post_form(
            token_endpoint,
            values,
            client_id=client_id,
            client_secret=client_secret if isinstance(client_secret, str) else None,
            auth_method=auth_method,
        )
        merged = dict(bundle)
        merged.update(token)
        if "refresh_token" not in token:
            merged["refresh_token"] = refresh_token
        return merged

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
        return response.status_code in {200, 202, 204, 404}

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
                    "clientInfo": {"name": "agent-foundation", "version": "1"},
                },
            },
            sensitive=False,
        )
        if response.status_code != 401:
            return {}
        value = response.headers.get("www-authenticate", "")
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
        candidates = [advertised] if advertised is not None else _resource_metadata_urls(endpoint)
        for candidate in candidates:
            if candidate is None:
                continue
            try:
                return await self._get_json(candidate)
            except MCPOAuthError as error:
                if advertised is not None or error.code != "metadata_not_found":
                    raise
        raise MCPOAuthError("protected_resource_metadata_missing")

    async def _authorization_metadata(self, issuer: str) -> dict[str, Any]:
        parsed = urlsplit(issuer)
        base = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        path = parsed.path.rstrip("/")
        candidates = (
            f"{base}/.well-known/oauth-authorization-server{path}",
            f"{issuer.rstrip('/')}/.well-known/openid-configuration",
        )
        for candidate in candidates:
            try:
                metadata = await self._get_json(candidate)
            except MCPOAuthError as error:
                if error.code == "metadata_not_found":
                    continue
                raise
            if metadata.get("issuer") != issuer:
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

    async def _post_form(
        self,
        endpoint: str,
        value: dict[str, str],
        *,
        client_id: str,
        client_secret: str | None,
        auth_method: str,
    ) -> dict[str, Any]:
        target = await self._validate(endpoint)
        headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
        if auth_method == "client_secret_post":
            if client_secret is None:
                raise MCPOAuthError("invalid_client_registration")
            value["client_secret"] = client_secret
        elif auth_method == "client_secret_basic":
            if client_secret is None:
                raise MCPOAuthError("invalid_client_registration")
            basic = f"{quote(client_id, safe='')}:{quote(client_secret, safe='')}".encode()
            headers["Authorization"] = f"Basic {base64.b64encode(basic).decode()}"
        elif auth_method != "none":
            raise MCPOAuthError("unsupported_token_endpoint_auth_method")
        response = await self._request(
            "POST",
            target,
            headers=headers,
            content=urlencode(value).encode(),
            sensitive=True,
        )
        if response.status_code != 200:
            try:
                error = (await self._json_body(response)).get("error")
            except MCPOAuthError:
                error = None
            if error in {"invalid_grant", "invalid_token", "insufficient_scope"}:
                raise MCPOAuthError("reauthorization_required", action_required=True)
            raise MCPOAuthError(
                "token_exchange_unavailable" if response.status_code >= 500 else "token_exchange_failed"
            )
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
            return await self._policy.validate(endpoint, resolve_dns=True)
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
    redirect_uri: str,
    state: str,
    code_challenge: str,
) -> str:
    values = {
        "response_type": "code",
        "client_id": preparation.client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": preparation.resource_url,
    }
    if preparation.scope is not None:
        values["scope"] = preparation.scope
    separator = "&" if urlsplit(preparation.authorization_endpoint).query else "?"
    return f"{preparation.authorization_endpoint}{separator}{urlencode(values, quote_via=quote)}"


def _resource_metadata_urls(endpoint: str) -> tuple[str, ...]:
    parsed = urlsplit(endpoint)
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    path = parsed.path or ""
    path_location = f"{origin}/.well-known/oauth-protected-resource{path}"
    root_location = f"{origin}/.well-known/oauth-protected-resource"
    return (path_location,) if path_location == root_location else (path_location, root_location)


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
    token_auth_method = value.get("token_endpoint_auth_method", "none")
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
    access_token = token.get("access_token")
    token_type = token.get("token_type")
    if (
        not isinstance(access_token, str)
        or not access_token
        or not isinstance(token_type, str)
        or token_type.lower() != "bearer"
    ):
        raise MCPOAuthError("invalid_token_response")
    bundle = {
        "kind": "oauth",
        "access_token": access_token,
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
