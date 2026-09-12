"""Application-owned authorization with a narrowly scoped browser handoff."""

from __future__ import annotations

import hmac
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.connectivity.management import fingerprint, record_command, replay_command
from a13n_service.durable_operations.idempotency import IdempotencyConflict
from a13n_service.iam import AuthenticatedActor
from a13n_service.ids import new_object_id
from a13n_service.secrets import SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from ..connectors.connections import ConnectorConnectionService
from ..mcp.domain import ReplaceMCPCredentialsRequest
from ..mcp.models import MCPAuthorizationRecord
from ..mcp.oauth_service import MCPOAuthService
from ..mcp.service import MCPConnectionService
from .access import ConnectionError, audit, authorize, require_connection, require_version
from .domain import (
    Authorization,
    AuthorizationAction,
    AuthorizationRedirect,
    CompleteAuthorizationRequest,
    CreateAuthorizationRequest,
    LaunchAuthorizationRequest,
    ReceiveAuthorizationRequest,
)
from .handoff import BrowserHandoff, digest, secret_bundle, store_material
from .models import AuthorizationRecord, ConnectionRecord
from .service import idempotency_digest

_TERMINAL = frozenset({"completed", "failed", "expired", "cancelled"})


class AuthorizationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        protector: SecretProtector,
        connectors: ConnectorConnectionService,
        mcp: MCPOAuthService,
        mcp_connections: MCPConnectionService,
        *,
        public_origin: str | None,
        return_urls: tuple[str, ...],
        clock: Clock = utc_now,
    ) -> None:
        self._sessions, self._protector = sessions, protector
        self._connectors, self._mcp = connectors, mcp
        self._mcp_connections = mcp_connections
        self._origin = public_origin
        self._return_urls = return_urls
        self._clock = clock

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        idempotency_key: str,
        request: CreateAuthorizationRequest,
    ) -> Authorization:
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id)
            await authorize(session, actor, record.workspace_id, mode="manage")
            kind = record.kind
        if request.method != "browser":
            if kind == "mcp":
                return await self._authorize_mcp_credentials(
                    actor=actor, connection_id=connection_id, idempotency_key=idempotency_key, request=request
                )
            if request.method != "credentials" or request.credentials is None:
                raise ConnectionError(
                    "unsupported_authorization_method",
                    "This source does not support the requested authorization method.",
                    category=ErrorCategory.invalid_request,
                )
            launch = await self._connectors.start_setup(
                actor=actor,
                connection_id=connection_id,
                idempotency_key=idempotency_key,
                expected_version=request.expected_version,
                setup=request.options,
                return_url="",
                credentials={key: value.get_secret_value() for key, value in request.credentials.items()},
            )
            return await self.get(actor=actor, authorization_id=launch.attempt_id)
        if self._origin is None:
            raise ConnectionError(
                "authorization_unavailable",
                "Browser authorization requires a public origin.",
                category=ErrorCategory.unavailable,
            )
        if request.return_url not in (*self._return_urls, f"{self._origin}/connections/callback"):
            raise ConnectionError(
                "invalid_return_url",
                "Authorization return URL is not registered.",
                category=ErrorCategory.invalid_request,
            )
        handoff = BrowserHandoff.from_request(request)
        if kind == "connector":
            launch = await self._connectors.start_setup(
                actor=actor,
                connection_id=connection_id,
                idempotency_key=idempotency_key,
                expected_version=request.expected_version,
                setup=request.options,
                return_url=handoff.return_url,
                handoff=handoff,
            )
            identifier = launch.attempt_id
        else:
            if request.options:
                raise ConnectionError(
                    "invalid_authorization_options",
                    "Configure the MCP OAuth client before authorization.",
                    category=ErrorCategory.invalid_request,
                )
            launch = await self._mcp.authorize(
                actor=actor,
                connection_id=connection_id,
                idempotency_key=idempotency_key,
                expected_version=request.expected_version,
                handoff=handoff,
            )
            identifier = launch.id
        return await self.get(actor=actor, authorization_id=identifier)

    async def _authorize_mcp_credentials(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        idempotency_key: str,
        request: CreateAuthorizationRequest,
    ) -> Authorization:
        if request.options:
            raise ConnectionError(
                "invalid_authorization_options",
                "Configure the MCP source before authorization.",
                category=ErrorCategory.invalid_request,
            )
        key = idempotency_digest(idempotency_key)
        request_fingerprint = fingerprint(
            request,
            credentials={name: value.get_secret_value() for name, value in request.credentials.items()}
            if request.credentials is not None
            else None,
        )
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            await authorize(session, actor, connection.workspace_id, mode="manage")
            try:
                replay = await replay_command(
                    session,
                    actor=actor,
                    workspace_id=connection.workspace_id,
                    operation="connection.authorize",
                    scope_id=connection.id,
                    idempotency_key_digest=key,
                    fingerprint=request_fingerprint,
                    now=now,
                )
            except IdempotencyConflict as error:
                raise ConnectionError(
                    "idempotency_conflict",
                    "Idempotency key was used for another request.",
                    category=ErrorCategory.conflict,
                ) from error
            if replay is not None:
                attempt = await session.get(AuthorizationRecord, replay.resource_id)
                if attempt is None:
                    raise invalid_handoff()
                return self._project(connection, attempt)
            require_version(connection, request.expected_version)
            if connection.status == "disabled":
                raise ConnectionError("connection_disabled", "Connection is disabled.", category=ErrorCategory.conflict)
            if (request.method == "client_credentials" and connection.auth_mode != "oauth") or (
                request.method == "credentials" and connection.auth_mode not in {"bearer", "static_headers"}
            ):
                raise ConnectionError(
                    "invalid_auth_mode",
                    "Authorization method does not match the MCP source.",
                    category=ErrorCategory.invalid_request,
                )
            connection.setup_generation += 1
            connection.version += 1
            version = connection.version
            authorization_generation = connection.authorization_generation
            mode = connection.auth_mode
            connection.status = "pending"
            connection.status_reason = None
            connection.updated_at = now
            attempt = MCPAuthorizationRecord(
                id=new_object_id("authz"),
                connection_id=connection.id,
                organization_id=connection.organization_id,
                workspace_id=connection.workspace_id,
                generation=connection.setup_generation,
                connection_version=connection.version,
                initiating_principal_type=actor.principal.principal_type.value,
                initiating_principal_id=actor.principal.principal_id,
                completion_method="credentials",
                status="starting",
                available_at=now,
                expires_at=now + timedelta(minutes=10),
                created_at=now,
                updated_at=now,
            )
            session.add(attempt)
            identifier = attempt.id
            record_command(
                session,
                actor=actor,
                organization_id=connection.organization_id,
                workspace_id=connection.workspace_id,
                operation="connection.authorize",
                scope_id=connection.id,
                idempotency_key_digest=key,
                fingerprint=request_fingerprint,
                resource_type="connection_authorization",
                resource_id=identifier,
                result_version=connection.version,
                now=now,
                resource=None,
            )
        error_code = None
        try:
            if request.method == "client_credentials":
                await self._mcp.authenticate_client_credentials(
                    actor=actor, connection_id=connection_id, idempotency_key=identifier, expected_version=version
                )
            else:
                assert request.credentials is not None
                if mode == "bearer" and set(request.credentials) != {"bearer"}:
                    raise ConnectionError(
                        "invalid_credentials",
                        "Bearer authorization requires one bearer credential.",
                        category=ErrorCategory.invalid_request,
                    )
                replacement = ReplaceMCPCredentialsRequest(
                    expected_version=version,
                    bearer=request.credentials.get("bearer") if mode == "bearer" else None,
                    static_headers=request.credentials if mode == "static_headers" else None,
                )
                await self._mcp_connections.replace_credentials(
                    actor=actor, connection_id=connection_id, idempotency_key=identifier, request=replacement
                )
        except ApplicationError as error:
            error_code = error.code
        async with transaction(self._sessions) as session:
            connection, attempt = await self._load(session, identifier, lock=True)
            if attempt.status == "starting":
                current = connection.setup_generation == attempt.generation
                attempt.status = (
                    "completed"
                    if current and connection.authorization_generation == authorization_generation + 1
                    else "failed"
                )
                attempt.last_error_code = error_code if current else "authorization_superseded"
                attempt.updated_at = self._clock()
                if attempt.status == "completed":
                    attempt.consumed_at = self._clock()
            return self._project(connection, attempt)

    async def get(self, *, actor: AuthenticatedActor, authorization_id: str) -> Authorization:
        async with transaction(self._sessions) as session:
            connection, attempt = await self._load(session, authorization_id, lock=False)
            await self._authorize(session, actor, attempt)
            return self._project(connection, attempt)

    async def cancel(self, *, actor: AuthenticatedActor, authorization_id: str) -> Authorization:
        async with transaction(self._sessions) as session:
            connection, attempt = await self._load(session, authorization_id, lock=True)
            await self._authorize(session, actor, attempt)
            if attempt.status not in _TERMINAL:
                attempt.status = "cancelled"
                attempt.clear_credential()
                attempt.claim_owner = None
                attempt.claim_expires_at = None
                attempt.updated_at = self._clock()
                session.add(audit(actor, connection, action="connection.authorization.cancel", now=attempt.updated_at))
            return self._project(connection, attempt)

    async def launch(self, authorization_id: str, request: LaunchAuthorizationRequest) -> AuthorizationRedirect:
        async with transaction(self._sessions) as session:
            connection, attempt = await self._load(session, authorization_id, lock=True)
            self._require_live(connection, attempt)
            if not matches(attempt.launch_token_digest, request.token):
                raise invalid_handoff()
            browser_binding = digest(request.browser_nonce)
            if attempt.browser_binding_digest is not None and not hmac.compare_digest(
                attempt.browser_binding_digest, browser_binding
            ):
                raise invalid_handoff()
            bundle = secret_bundle(attempt, self._protector)
            url = bundle.get("provider_url")
            if not isinstance(url, str):
                raise ConnectionError(
                    "authorization_preparing", "Authorization is still preparing.", category=ErrorCategory.conflict
                )
            attempt.browser_binding_digest = browser_binding
            store_material(attempt, self._protector, browser_nonce=request.browser_nonce)
            return AuthorizationRedirect(url=url)

    async def receive(self, authorization_id: str, request: ReceiveAuthorizationRequest) -> AuthorizationRedirect:
        async with transaction(self._sessions) as session:
            connection, attempt = await self._load(session, authorization_id, lock=True)
            self._require_live(connection, attempt)
            if not matches(attempt.browser_binding_digest, request.browser_nonce):
                raise invalid_handoff()
            if attempt.kind == "mcp":
                if attempt.status != "received" or request.session_uri is not None:
                    raise invalid_handoff()
            elif attempt.status != "attached" or (
                attempt.completion_method == "oauth_verifier" and request.session_uri is None
            ):
                raise invalid_handoff()
            bundle = secret_bundle(attempt, self._protector)
            existing = bundle.get("session_uri")
            if existing is not None and existing != request.session_uri:
                raise invalid_handoff()
            receipt = bundle.get("application_receipt")
            if not isinstance(receipt, str):
                receipt = secrets.token_urlsafe(32)
                attempt.receipt_digest = digest(receipt)
                store_material(
                    attempt,
                    self._protector,
                    application_receipt=receipt,
                    **({"session_uri": request.session_uri} if request.session_uri is not None else {}),
                )
            assert attempt.return_url is not None and attempt.client_state is not None
            query = urlencode({"authorization_id": attempt.id, "state": attempt.client_state, "receipt": receipt})
            return AuthorizationRedirect(url=f"{attempt.return_url}{'&' if '?' in attempt.return_url else '?'}{query}")

    async def complete(
        self, *, actor: AuthenticatedActor, authorization_id: str, request: CompleteAuthorizationRequest
    ) -> Authorization:
        async with transaction(self._sessions) as session:
            connection, attempt = await self._load(session, authorization_id, lock=True)
            await self._authorize(session, actor, attempt)
            if not matches(attempt.receipt_digest, request.receipt) or not matches(
                attempt.completion_challenge, request.completion_verifier
            ):
                raise invalid_handoff()
            if attempt.status == "completed":
                return self._project(connection, attempt)
            self._require_live(connection, attempt)
            kind = attempt.kind
            bundle = secret_bundle(attempt, self._protector)
        if kind == "mcp":
            state, receipt = bundle.get("state"), bundle.get("protocol_receipt")
            if not isinstance(state, str) or not isinstance(receipt, str):
                raise invalid_handoff()
            await self._mcp.callback(actor=actor, state=state, receipt=receipt)
        else:
            nonce, session_uri = bundle.get("browser_nonce"), bundle.get("session_uri")
            if not isinstance(nonce, str) or (session_uri is not None and not isinstance(session_uri, str)):
                raise invalid_handoff()
            await self._connectors.setup_coordinator.complete_callback(
                actor=actor, attempt_id=authorization_id, browser_nonce=nonce, session_uri=session_uri
            )
        return await self.get(actor=actor, authorization_id=authorization_id)

    async def _load(
        self, session: AsyncSession, identifier: str, *, lock: bool
    ) -> tuple[ConnectionRecord, AuthorizationRecord]:
        attempt = await session.get(AuthorizationRecord, identifier)
        if attempt is None:
            raise ConnectionError(
                "resource_not_found", "The requested resource was not found.", category=ErrorCategory.not_found
            )
        connection = await require_connection(session, attempt.connection_id, lock=lock)
        if lock:
            await session.refresh(attempt, with_for_update=True)
        return connection, attempt

    async def _authorize(self, session: AsyncSession, actor: AuthenticatedActor, attempt: AuthorizationRecord) -> None:
        await authorize(session, actor, attempt.workspace_id, mode="manage")
        if (
            attempt.initiating_principal_type != actor.principal.principal_type.value
            or attempt.initiating_principal_id != actor.principal.principal_id
        ):
            raise ConnectionError(
                "resource_not_found", "The requested resource was not found.", category=ErrorCategory.not_found
            )

    def _require_live(self, connection: ConnectionRecord, attempt: AuthorizationRecord) -> None:
        if (
            attempt.status in _TERMINAL
            or assume_utc(attempt.expires_at) <= self._clock()
            or connection.setup_generation != attempt.generation
            or connection.status == "disabled"
        ):
            raise ConnectionError(
                "authorization_unavailable", "Authorization is no longer available.", category=ErrorCategory.conflict
            )

    def _project(self, connection: ConnectionRecord, attempt: AuthorizationRecord) -> Authorization:
        status = attempt.status
        if status not in _TERMINAL and assume_utc(attempt.expires_at) <= self._clock():
            status = "expired"
        mapped = {
            "pending": "awaiting_user",
            "starting": "preparing",
            "attached": "processing" if attempt.completion_method == "polling" else "awaiting_user",
            "received": "awaiting_completion",
            "reserved": "processing",
            "exchanging": "processing",
        }
        action = None
        if status in {"attached", "pending"} and attempt.ciphertext is not None:
            bundle = secret_bundle(attempt, self._protector)
            token = bundle.get("launch_token")
            if isinstance(token, str) and isinstance(bundle.get("provider_url"), str):
                action = AuthorizationAction(
                    type="open_url",
                    url=f"{self._origin}/connection-authorizations/browser#"
                    + urlencode({"authorization_id": attempt.id, "token": token}),
                )
        if status == "completed" and connection.status != "ready":
            action = AuthorizationAction(type="check_connection")
        outcome_unknown = attempt.last_error_code in {"setup_outcome_unknown", "setup_verification_pending"}
        if status in {"failed", "expired", "cancelled"} and not outcome_unknown:
            action = AuthorizationAction(type="restart")
        return Authorization.model_validate(
            dict(
                id=attempt.id,
                connection_id=attempt.connection_id,
                status=mapped.get(status, status),
                next_action=action,
                error_code=attempt.last_error_code,
                outcome_unknown=outcome_unknown,
                expires_at=assume_utc(attempt.expires_at),
                updated_at=assume_utc(attempt.updated_at),
            )
        )


def matches(expected: str | None, value: str) -> bool:
    return expected is not None and hmac.compare_digest(expected, digest(value))


def invalid_handoff() -> ConnectionError:
    return ConnectionError(
        "invalid_authorization_handoff", "Authorization handoff is invalid.", category=ErrorCategory.invalid_request
    )
