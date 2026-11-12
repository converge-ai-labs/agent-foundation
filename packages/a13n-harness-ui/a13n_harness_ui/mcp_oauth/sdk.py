"""Bounded compatibility seam for MCP's refresh-only protected helpers.

The SDK owns protocol parsing, token endpoint authentication, resource indicators,
and rotated-token inheritance. The application owns durable expiry and commits.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx2
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import ValidationError

from .models import OAuthBundle, OAuthError, require_scopes


class BundleStorage:
    def __init__(self, bundle: OAuthBundle, commit: Callable[[OAuthBundle], Awaitable[None]]) -> None:
        self.bundle = bundle.model_copy(deep=True)
        self._commit = commit

    async def get_tokens(self) -> OAuthToken | None:
        return self.bundle.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        import time

        require_scopes(tokens, self.bundle.requested_scope)
        if not tokens.access_token:
            raise OAuthError("The provider returned an empty access token; reconnect.", code="mcp_oauth_reconnect")
        self.bundle.tokens = tokens
        self.bundle.expires_at = None if tokens.expires_in is None else time.time() + tokens.expires_in
        await self._commit(self.bundle)

    async def get_client_info(self) -> OAuthClientInformationFull:
        return self.bundle.client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        raise RuntimeError("Runtime refresh cannot register a client")


async def refresh(
    bundle: OAuthBundle,
    client: httpx2.AsyncClient,
    commit: Callable[[OAuthBundle], Awaitable[None]],
) -> OAuthToken:
    storage = BundleStorage(bundle, commit)
    provider = OAuthClientProvider(
        bundle.resource_url,
        OAuthClientMetadata(redirect_uris=bundle.client.redirect_uris, scope=bundle.requested_scope),
        storage,
    )
    context = provider.context
    context.client_info = bundle.client
    context.current_tokens = bundle.tokens
    context.token_expiry_time = bundle.expires_at
    context.oauth_metadata = bundle.metadata
    context.protected_resource_metadata = bundle.resource_metadata
    context.auth_server_url = bundle.issuer
    context.protocol_version = bundle.protocol_version
    request = await provider._refresh_token()
    response = await client.send(request)
    # Never enter the SDK's interactive fallback or resend a business request.
    if response.status_code != 200:
        raise OAuthError("The token refresh failed; reconnect to this MCP server.", code="mcp_oauth_reconnect")
    try:
        OAuthToken.model_validate_json(response.content)
    except ValidationError:
        # Avoid the SDK's validation logger echoing a credential-bearing response.
        raise OAuthError(
            "The token refresh returned an invalid response; reconnect.", code="mcp_oauth_reconnect"
        ) from None
    if not await provider._handle_refresh_response(response):
        raise OAuthError("The token refresh returned an invalid response; reconnect.", code="mcp_oauth_reconnect")
    assert storage.bundle.tokens is not None
    return storage.bundle.tokens
