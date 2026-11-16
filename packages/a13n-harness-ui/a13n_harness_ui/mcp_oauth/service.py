"""Application-owned MCP OAuth: durable grants and noninteractive runtime authentication."""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx2
from a13n_harness.http import outbound_tls_verify
from anyio import CancelScope, fail_after, sleep, to_thread
from filelock import FileLock, Timeout

from a13n_harness_ui.configuration.models import McpRemoteTransport
from a13n_harness_ui.storage.mcp_oauth import McpOAuthRepository

from .models import OAuthBinding, OAuthError
from .sdk import refresh

if TYPE_CHECKING:
    from a13n_harness_ui.composition.models import ResolvedAgentNode, ResolvedMcpRecipe
    from a13n_harness_ui.configuration.models import LoadedHarnessUiConfiguration


def require_secure_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise OAuthError("MCP OAuth endpoints must use credential-free HTTPS URLs.", code="mcp_oauth_endpoint_invalid")


def host_client() -> httpx2.AsyncClient:
    async def validate(request: httpx2.Request) -> None:
        require_secure_url(str(request.url))

    return httpx2.AsyncClient(
        verify=outbound_tls_verify(),
        timeout=30,
        follow_redirects=False,
        event_hooks={"request": [validate]},
    )


class McpOAuth:
    def __init__(
        self,
        repository: McpOAuthRepository,
        root: Path,
        configuration_root: Path,
        *,
        client_factory: Callable[[], httpx2.AsyncClient] = host_client,
    ) -> None:
        self.repository = repository
        self.root = root
        self.configuration_root = configuration_root
        self.owner_prefix = f"mcp:{sha256(str(configuration_root.resolve()).encode()).hexdigest()}:"
        self.client_factory = client_factory
        self.cancel_logins: Callable[[str], Awaitable[None]] | None = None

    def owner(self, server_id: str) -> str:
        return self.owner_prefix + server_id

    async def bind(
        self, recipe: ResolvedMcpRecipe, *, owner: str | None = None, replace_configuration: bool = False
    ) -> ResolvedMcpRecipe:
        transport = recipe.transport
        if not isinstance(transport, McpRemoteTransport) or transport.oauth is None or recipe.oauth_binding is not None:
            return recipe
        digest = sha256(transport.model_dump_json().encode()).hexdigest()
        binding = await self.repository.bind(
            owner or self.owner(recipe.server_id), digest, replace_configuration=replace_configuration
        )
        return recipe.model_copy(update={"oauth_binding": binding})

    async def reconcile(self, source: LoadedHarnessUiConfiguration) -> None:
        owners: set[str] = set()
        for identity, resource in source.mcp_servers.items():
            transport = resource.transport
            if isinstance(transport, McpRemoteTransport) and transport.oauth is not None:
                owner = self.owner(identity)
                owners.add(owner)
                await self.repository.bind(
                    owner, sha256(transport.model_dump_json().encode()).hexdigest(), replace_configuration=True
                )
        await self.repository.reconcile(self.owner_prefix, owners)

    async def bind_node(self, node: ResolvedAgentNode) -> ResolvedAgentNode:
        return node.model_copy(
            update={
                "mcp_servers": tuple([await self.bind(recipe) for recipe in node.mcp_servers]),
                "children": tuple(
                    [
                        child.model_copy(update={"definition": await self.bind_node(child.definition)})
                        for child in node.children
                    ]
                ),
            }
        )

    def auth(self, binding: OAuthBinding, resource_url: str) -> httpx2.Auth:
        return _Bearer(self, binding, resource_url)

    async def access_token(self, binding: OAuthBinding) -> str:
        account = await self.repository.get(binding)
        bundle = account.bundle
        if bundle is None or bundle.tokens is None:
            raise OAuthError("Connect to this MCP server in WebUI Settings first.", code="mcp_oauth_login_required")
        if not account.refresh_pending and (bundle.expires_at is None or time.time() < bundle.expires_at - 30):
            return bundle.tokens.access_token
        async with self._refresh_lock(binding):
            account = await self.repository.get(binding)
            bundle = account.bundle
            if account.refresh_pending:
                raise OAuthError("The previous refresh has an unknown outcome; reconnect.", code="mcp_oauth_reconnect")
            if bundle is None or bundle.tokens is None:
                raise OAuthError("Connect to this MCP server first.", code="mcp_oauth_login_required")
            if bundle.expires_at is None or time.time() < bundle.expires_at - 30:
                return bundle.tokens.access_token
            if not bundle.tokens.refresh_token:
                raise OAuthError("The MCP authorization expired; reconnect.", code="mcp_oauth_reconnect")

            async def commit(updated):
                await self.repository.save(binding, updated, expected_version=account.credential_version)

            # All uncertain outcomes retain this marker, including cancellation and commit failure.
            await self.repository.begin_refresh(binding, expected_version=account.credential_version)
            async with self.client_factory() as client:
                try:
                    tokens = await refresh(bundle, client, commit)
                except httpx2.HTTPError:
                    raise OAuthError(
                        "The token refresh could not complete; reconnect.", code="mcp_oauth_reconnect"
                    ) from None
            return tokens.access_token

    @asynccontextmanager
    async def _refresh_lock(self, binding: OAuthBinding) -> AsyncGenerator[None]:
        directory = self.root / "mcp-oauth-locks"
        await to_thread.run_sync(lambda: directory.mkdir(mode=0o700, parents=True, exist_ok=True))
        lock = FileLock(directory / f"{binding.binding_id}.lock", timeout=0, thread_local=False, mode=0o600)
        acquired = False
        try:
            with fail_after(30):
                while not acquired:
                    try:
                        await to_thread.run_sync(lock.acquire)
                        acquired = True
                    except Timeout:
                        await sleep(0.05)
            yield
        finally:
            if acquired:
                with CancelScope(shield=True):
                    await to_thread.run_sync(lock.release)


class _Bearer(httpx2.Auth):
    def __init__(self, service: McpOAuth, binding: OAuthBinding, resource_url: str) -> None:
        self._service = service
        self._binding = binding
        self._resource = urlsplit(resource_url)

    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        target = urlsplit(str(request.url))
        if (target.scheme, target.netloc) != (self._resource.scheme, self._resource.netloc):
            raise OAuthError("MCP credentials cannot be sent to another origin.", code="mcp_oauth_endpoint_invalid")
        request.headers["Authorization"] = f"Bearer {await self._service.access_token(self._binding)}"
        yield request
