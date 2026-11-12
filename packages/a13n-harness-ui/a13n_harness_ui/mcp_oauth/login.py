"""Explicit WebUI DCR login; pending browser state is process-local and short lived."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx2
from anyio import Event, fail_after
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl

from a13n_harness_ui.composition.models import ResolvedMcpRecipe
from a13n_harness_ui.configuration.models import McpRemoteTransport

from .models import OAuthBinding, OAuthBundle, OAuthError, OAuthLoginStatus, require_scopes
from .service import McpOAuth, require_secure_url

CALLBACK_PATH = "/oauth/mcp/callback"


@dataclass
class _Attempt:
    binding: OAuthBinding
    origin: str
    owner: str
    cookie: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    attempt_id: str = field(default_factory=lambda: uuid4().hex)
    state: Literal["connecting", "waiting", "connected", "failed", "cancelled"] = "connecting"
    authorization_url: str | None = None
    error: str | None = None
    callback: AuthorizationCodeResult | None = field(default=None, repr=False)
    ready: Event = field(default_factory=Event)
    received: Event = field(default_factory=Event)
    task: asyncio.Task[None] | None = None
    created_at: float = field(default_factory=time.monotonic)

    def status(self) -> OAuthLoginStatus:
        return OAuthLoginStatus(
            attempt_id=self.attempt_id, state=self.state, authorization_url=self.authorization_url, error=self.error
        )


class Logins:
    def __init__(self, service: McpOAuth) -> None:
        self.service = service
        service.cancel_logins = self.cancel_owner
        self._attempts: dict[str, _Attempt] = {}

    async def start(self, recipe: ResolvedMcpRecipe, origin: str) -> tuple[OAuthLoginStatus, str]:
        if (
            recipe.oauth_binding is None
            or not isinstance(recipe.transport, McpRemoteTransport)
            or recipe.transport.oauth is None
        ):
            raise OAuthError("This server is not configured for MCP OAuth.", code="mcp_oauth_not_configured")
        # Bound pending state; finished records remain available for polling for ten minutes.
        for identity, old in tuple(self._attempts.items()):
            if time.monotonic() - old.created_at > 600:
                await self.cancel(identity)
                self._attempts.pop(identity, None)
        if len(self._attempts) >= 32:
            raise OAuthError("Too many pending MCP logins; cancel an existing login.", code="mcp_oauth_login_limit")
        account = await self.service.repository.get(recipe.oauth_binding)
        for old in tuple(self._attempts.values()):
            if old.binding.binding_id == recipe.oauth_binding.binding_id and old.state in {"connecting", "waiting"}:
                await self.cancel(old.attempt_id)
        # Re-read after cancellation, which may have retired the same generation.
        current = await self.service.repository.bind(
            account.owner,
            account.configuration_digest,
        )
        binding = await self.service.repository.replace(current)
        attempt = _Attempt(binding, origin, account.owner)
        self._attempts[attempt.attempt_id] = attempt
        attempt.task = asyncio.create_task(self._run(attempt, recipe.transport))
        await attempt.ready.wait()
        return attempt.status(), attempt.cookie

    def current(self, owner: str) -> OAuthLoginStatus | None:
        matching = [
            attempt
            for attempt in self._attempts.values()
            if attempt.owner == owner and time.monotonic() - attempt.created_at < 600
        ]
        return matching[-1].status() if matching else None

    def status(self, attempt_id: str) -> OAuthLoginStatus:
        return self._get(attempt_id).status()

    def _get(self, attempt_id: str) -> _Attempt:
        attempt = self._attempts.get(attempt_id)
        if attempt is None:
            raise OAuthError("This login is unavailable; start again.", code="mcp_oauth_login_missing")
        return attempt

    async def cancel(self, attempt_id: str) -> None:
        attempt = self._get(attempt_id)
        if attempt.state not in {"connecting", "waiting"}:
            return
        attempt.state = "cancelled"
        if attempt.task is not None:
            attempt.task.cancel()
            await asyncio.gather(attempt.task, return_exceptions=True)
        try:
            await self.service.repository.replace(attempt.binding)
        except OAuthError:
            pass  # Removal/replacement already invalidated this attempt.
        attempt.ready.set()

    def receive(
        self, *, state: str, cookie: str | None, origin: str, code: str, issuer: str | None, error: str | None
    ) -> str:
        for attempt in self._attempts.values():
            expected = parse_qs(urlsplit(attempt.authorization_url or "").query).get("state", [""])[0]
            if not expected or not secrets.compare_digest(expected, state):
                continue
            if (
                attempt.state != "waiting"
                or attempt.received.is_set()
                or time.monotonic() - attempt.created_at > 600
                or origin != attempt.origin
                or not cookie
                or not secrets.compare_digest(cookie, attempt.cookie)
            ):
                break
            if error or not code:
                attempt.error = "Authorization was declined; start again."
                attempt.received.set()
            else:
                attempt.callback = AuthorizationCodeResult(code=code, state=state, iss=issuer)
                attempt.received.set()
            return attempt.attempt_id
        raise OAuthError("This authorization callback is invalid or expired.", code="mcp_oauth_callback_invalid")

    def cookie_name(self, state: str) -> str:
        for attempt in self._attempts.values():
            expected = parse_qs(urlsplit(attempt.authorization_url or "").query).get("state", [""])[0]
            if expected and secrets.compare_digest(expected, state):
                return f"mcpa_{attempt.attempt_id}"
        return "mcpa_missing"

    async def cancel_owner(self, owner: str) -> None:
        for identity, attempt in tuple(self._attempts.items()):
            if attempt.owner == owner:
                await self.cancel(identity)

    async def close(self) -> None:
        for identity in tuple(self._attempts):
            await self.cancel(identity)
        self._attempts.clear()

    async def _run(self, attempt: _Attempt, transport: McpRemoteTransport) -> None:
        assert transport.oauth is not None
        requested_scope = transport.oauth.scope

        class Provider(OAuthClientProvider):
            async def _perform_authorization(self) -> httpx2.Request:
                if requested_scope is not None:
                    self.context.client_metadata.scope = requested_scope
                return await super()._perform_authorization()

        async def redirect(url: str) -> None:
            require_secure_url(url)
            attempt.authorization_url = url
            attempt.state = "waiting"
            attempt.ready.set()

        async def callback() -> AuthorizationCodeResult:
            await attempt.received.wait()
            if attempt.callback is None:
                raise OAuthError("Authorization was declined; start again.", code="mcp_oauth_login_declined")
            return attempt.callback

        metadata = OAuthClientMetadata(
            redirect_uris=[AnyUrl(attempt.origin + CALLBACK_PATH)],
            client_name="Harness UI",
            token_endpoint_auth_method="none",
            scope=requested_scope,
            application_type="native"
            if urlsplit(attempt.origin).hostname in {"localhost", "127.0.0.1", "::1"}
            else "web",
        )
        storage = _LoginStorage(self.service, attempt.binding, transport.url, requested_scope)
        provider = Provider(transport.url, metadata, storage, redirect_handler=redirect, callback_handler=callback)
        storage.provider = provider
        request = httpx2.Request(
            "POST",
            transport.url,
            headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2026-07-28",
                    "capabilities": {},
                    "clientInfo": {"name": "Harness UI", "version": "1"},
                },
            },
        )
        flow = provider.async_auth_flow(request)
        registration_prepared = False
        try:
            from a13n_harness_ui.mcp_adapters import resolve_mcp_values

            # Authored tenant/routing headers belong only to the original MCP resource.
            # Discovery, registration and token requests are constructed separately by the SDK.
            request.headers.update(await resolve_mcp_values(transport.headers, self.service.configuration_root))
            with fail_after(600):
                async with self.service.client_factory() as client:
                    outbound = await anext(flow)
                    while True:
                        if storage.connected:
                            # Code exchange committed; subsequent MCP initialization is a separate check.
                            attempt.state = "connected"
                            break
                        context = provider.context
                        if outbound.method == "POST" and outbound is not request:
                            discovered = context.oauth_metadata
                            if discovered is None or discovered.registration_endpoint is None:
                                raise OAuthError(
                                    "The provider does not advertise DCR; this server is unsupported.",
                                    code="mcp_oauth_dcr_required",
                                )
                            if (
                                context.client_info is None
                                and requested_scope is not None
                                and not registration_prepared
                            ):
                                from mcp.client.auth.utils import create_client_registration_request

                                context.client_metadata.scope = requested_scope
                                outbound = create_client_registration_request(
                                    discovered, context.client_metadata, transport.url
                                )
                                registration_prepared = True
                        require_secure_url(str(outbound.url))
                        response = await client.send(outbound)
                        if outbound.method == "POST" and outbound is not request:
                            # The SDK logs exceptions with upstream response material.
                            # Validate credential responses before entering its generator.
                            _validate_credential_response(response, registration=context.client_info is None)
                        try:
                            outbound = await flow.asend(response)
                        except StopAsyncIteration:
                            if not storage.connected:
                                raise OAuthError(
                                    "The server did not complete OAuth authorization.", code="mcp_oauth_login_failed"
                                ) from None
                            attempt.state = "connected"
                            break
        except asyncio.CancelledError:
            raise
        except OAuthError as exc:
            attempt.state = "failed"
            attempt.error = str(exc)
        except Exception:
            # SDK errors may embed state, code, credentials, or provider response bodies.
            attempt.state = "failed"
            attempt.error = "Could not connect. Check DCR support and authorization, then start again."
        finally:
            await flow.aclose()
            attempt.ready.set()


class _LoginStorage:
    def __init__(self, service: McpOAuth, binding: OAuthBinding, resource: str, scope: str | None) -> None:
        self.service, self.binding, self.resource, self.scope = service, binding, resource, scope
        self.provider: OAuthClientProvider | None = None
        self.client: OAuthClientInformationFull | None = None
        self.version = 0
        self.connected = False

    async def get_tokens(self) -> OAuthToken | None:
        return None

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client = client_info
        await self._save(None)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        require_scopes(tokens, self.scope)
        if not tokens.access_token:
            raise OAuthError("The provider returned an empty access token.", code="mcp_oauth_login_failed")
        await self._save(tokens)
        self.connected = True

    async def _save(self, tokens: OAuthToken | None) -> None:
        assert self.provider is not None and self.client is not None
        context = self.provider.context
        if context.oauth_metadata is None or context.oauth_metadata.registration_endpoint is None:
            raise OAuthError("The provider does not advertise DCR.", code="mcp_oauth_dcr_required")
        bundle = OAuthBundle(
            resource_url=self.resource,
            issuer=str(context.oauth_metadata.issuer),
            metadata=context.oauth_metadata,
            resource_metadata=context.protected_resource_metadata,
            protocol_version=context.protocol_version,
            client=self.client,
            tokens=tokens,
            requested_scope=self.scope,
            expires_at=context.token_expiry_time if tokens is not None else None,
        )
        await self.service.repository.save(self.binding, bundle, expected_version=self.version)
        self.version += 1


def _validate_credential_response(response: httpx2.Response, *, registration: bool) -> None:
    if response.is_redirect:
        return  # SDK redirect checks still apply; every yielded URL must pass HTTPS validation.
    if response.status_code not in {200, 201}:
        raise OAuthError("The provider rejected authorization; start again.", code="mcp_oauth_login_failed")
    try:
        if registration:
            value = response.json()
            if isinstance(value, dict):
                value.pop("issuer", None)  # The SDK binds this field itself.
            OAuthClientInformationFull.model_validate(value)
        else:
            OAuthToken.model_validate_json(response.content)
    except ValueError:
        raise OAuthError("The provider returned invalid authorization data.", code="mcp_oauth_login_failed") from None
