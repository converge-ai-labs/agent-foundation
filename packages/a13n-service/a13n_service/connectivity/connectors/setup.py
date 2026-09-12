"""Short-transaction ConnectorConnection setup orchestration."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial

from anyio import fail_after, move_on_after
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.connectors.contracts import (
    AdapterConnectionStatus,
    AdapterStatusReason,
    ConnectionInspection,
    ConnectorProviderError,
    SetupCompletionMethod,
    SetupContext,
    SetupStarted,
)
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.domain import JsonObject
from a13n_service.iam import AuthenticatedActor, PrincipalType
from a13n_service.iam.domain import PrincipalRef
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.iam.resource_scope import ResourceScope
from a13n_service.ids import new_object_id
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .connection_access import (
    apply_inspection,
    authorize_connection,
    connection_resource,
    external_error,
    verify_inspection,
)
from .domain import ConnectorSetupLaunch
from .errors import ConnectorError
from .management import (
    ProviderSnapshot,
    audit,
    configure_provider,
    decode_credentials,
    require_active_provider,
    require_connection,
    require_connector_provider,
)
from .models import ConnectorConnectionRecord, ConnectorProviderRecord, ConnectorSetupAttemptRecord
from .shared_setup import reserve_shared_setup

logger = logging.getLogger("a13n_service.connectivity.connector_setup")


def browser_digest(nonce: str | None) -> str | None:
    if nonce is None:
        return None
    if re.fullmatch(r"[a-f0-9]{64}", nonce) is None:
        raise ConnectorError(
            "invalid_browser_binding", "Invalid browser authorization context.", category=ErrorCategory.invalid_request
        )
    return hashlib.sha256(nonce.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SetupSnapshot:
    id: str
    generation: int
    connector_key: str
    external_user_correlation: str
    external_ref: str | None
    setup_ref: str | None
    completion_method: str
    setup_json: JsonObject


@dataclass(frozen=True, slots=True)
class AttemptSnapshot:
    provider_id: str
    credential_generation: int
    attempt: SetupSnapshot
    connector: ProviderSnapshot
    credentials: JsonObject


class ConnectorSetupCoordinator:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: ConnectorProviderRegistry,
        protector: SecretProtector,
        *,
        correlation_secret: bytes | None,
        public_origin: str | None,
        setup_ttl_seconds: int,
        setup_lease_seconds: int = 60,
        clock: Clock = utc_now,
    ) -> None:
        if correlation_secret is not None and len(correlation_secret) < 32:
            raise ValueError("ConnectorProvider setup correlation secret must be at least 32 bytes")
        self._sessions = sessions
        self._adapters = adapters
        self._protector = protector
        self._correlation_secret = correlation_secret
        self._public_origin = public_origin.rstrip("/") if public_origin is not None else None
        self._setup_ttl_seconds = setup_ttl_seconds
        self._setup_lease_seconds = setup_lease_seconds
        self._clock = clock

    def new_attempt(
        self,
        connection: ConnectorConnectionRecord,
        *,
        connector: ConnectorProviderRecord,
        actor: AuthenticatedActor,
        attempt_id: str,
        setup: JsonObject,
        return_path: str,
        now: datetime,
        browser_nonce: str | None = None,
    ) -> ConnectorSetupAttemptRecord:
        binding = browser_digest(browser_nonce)
        if connector.type == "composio" and (
            binding is None or self._public_origin is None or not self._public_origin.startswith("https://")
        ):
            raise ConnectorError(
                "browser_setup_required",
                "Composio setup requires browser binding and a public HTTPS origin.",
                category=ErrorCategory.invalid_request,
            )
        correlation = self.correlation(connector, workspace_id=connection.workspace_id)
        return ConnectorSetupAttemptRecord(
            id=attempt_id,
            organization_id=connection.organization_id,
            workspace_id=connection.workspace_id,
            connector_connection_id=connection.id,
            generation=connection.setup_generation,
            initiating_principal_type=actor.principal.principal_type.value,
            initiating_principal_id=actor.principal.principal_id,
            type=connector.type,
            connector_key=connection.connector_key,
            external_user_correlation=correlation,
            return_path=return_path,
            setup_json=setup,
            external_ref=None,
            browser_binding_digest=binding,
            completion_method=SetupCompletionMethod.polling,
            status="starting",
            available_at=now,
            expires_at=now
            + timedelta(
                seconds=min(self._setup_ttl_seconds, 600) if connector.type == "composio" else self._setup_ttl_seconds
            ),
            reserved_at=None,
            consumed_at=None,
            attempt_count=0,
            claim_generation=1,
            claim_owner=new_object_id("csa"),
            claim_expires_at=now + timedelta(seconds=self._setup_lease_seconds),
            last_error_code=None,
            created_at=now,
            updated_at=now,
        )

    async def launch(
        self, attempt_id: str, connection_id: str, *, initial_claim: tuple[str, int] | None = None
    ) -> ConnectorSetupLaunch:
        async with short_session(self._sessions) as session:
            attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id)
            if attempt is None:
                raise ConnectorError(
                    "setup_unavailable", "ConnectorProvider setup is unavailable.", category=ErrorCategory.not_found
                )
            if attempt.status in {"attached", "reserved", "completed", "failed", "expired"}:
                return await self._receipt(session, attempt, connection_id=connection_id, redirect_url=None)
        try:
            started = await self.start_attempt(attempt_id, initial_claim=initial_claim)
        except ConnectorError as error:
            if error.code != "setup_in_progress":
                raise
            started = None
        async with short_session(self._sessions) as session:
            attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id)
            if attempt is None:
                raise ConnectorError(
                    "setup_unavailable", "ConnectorProvider setup is unavailable.", category=ErrorCategory.not_found
                )
            return await self._receipt(
                session,
                attempt,
                connection_id=connection_id,
                redirect_url=started.redirect_url if started is not None else None,
            )

    async def _receipt(
        self,
        session: AsyncSession,
        attempt: ConnectorSetupAttemptRecord,
        *,
        connection_id: str,
        redirect_url: str | None,
    ) -> ConnectorSetupLaunch:
        status = attempt.status if attempt.status in {"completed", "failed", "expired"} else "pending"
        return ConnectorSetupLaunch.model_validate(
            {
                "completion_method": attempt.completion_method,
                "attempt_id": attempt.id,
                "status": status,
                "expires_at": assume_utc(attempt.expires_at),
                "connection": await connection_resource(session, connection_id),
                "redirect_url": redirect_url,
            }
        )

    async def complete_callback(
        self,
        *,
        actor: AuthenticatedActor,
        attempt_id: str,
        browser_nonce: str,
        session_uri: str | None = None,
    ) -> str:
        if actor.principal.principal_type is not PrincipalType.user:
            raise ConnectorError(
                "interactive_user_required", "Interactive setup requires a User.", category=ErrorCategory.forbidden
            )
        digest = browser_digest(browser_nonce)
        now = self._clock()
        owner = new_object_id("csa")
        async with transaction(self._sessions) as session:
            connection, attempt = await _lock_setup(session, attempt_id)
            if (
                attempt is None
                or attempt.initiating_principal_id != actor.principal.principal_id
                or attempt.browser_binding_digest is None
                or digest is None
                or not hmac.compare_digest(attempt.browser_binding_digest, digest)
                or attempt.external_ref is None
            ):
                raise ConnectorError(
                    "invalid_callback", "Invalid browser authorization context.", category=ErrorCategory.invalid_request
                )
            await authorize_connection(session, actor, connection, mode="manage")
            self._verify_callback_proof(attempt, session_uri=session_uri)
            if attempt.status == "completed":
                if connection.setup_generation != attempt.generation or connection.status != "ready":
                    raise ConnectorError(
                        "setup_lost_race", "Setup is no longer current.", category=ErrorCategory.conflict
                    )
                return attempt.return_path
            await _require_eligible(session, attempt, connection, now=now)
            if attempt.status != "attached" or (
                attempt.claim_expires_at is not None and assume_utc(attempt.claim_expires_at) > now
            ):
                raise ConnectorError(
                    "setup_in_progress",
                    "Authorization verification is being reconciled.",
                    category=ErrorCategory.conflict,
                )
            attempt.claim_generation += 1
            generation = attempt.claim_generation
            attempt.claim_owner = owner
            attempt.claim_expires_at = now + timedelta(seconds=self._setup_lease_seconds)
            attempt.status = "reserved"
            attempt.reserved_at = now
            attempt.updated_at = now
            return_path = attempt.return_path
        logger.info("connector_setup_reserved", extra={"attempt_id": attempt_id, "claim_generation": generation})
        try:
            try:
                with fail_after(max(1, self._setup_lease_seconds - 5)):
                    inspection = await self._complete_attempt(attempt_id, session_uri=session_uri)
            except TimeoutError as error:
                raise ConnectorProviderError("provider_unavailable", outcome_unknown=True) from error
            await self.finish_attempt(
                attempt_id, inspection, actor=actor, claim_owner=owner, claim_generation=generation
            )
            async with short_session(self._sessions) as session:
                attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id)
                if attempt is not None and attempt.status in {"failed", "expired"}:
                    raise ConnectorError(
                        "setup_failed",
                        "The provider did not confirm an active account. Start authorization again.",
                        category=ErrorCategory.conflict,
                    )
                if attempt is None or attempt.status != "completed":
                    raise ConnectorError(
                        "setup_in_progress",
                        "Authorization verification is being reconciled.",
                        category=ErrorCategory.conflict,
                    )
            return return_path
        except ConnectorProviderError as error:
            logger.warning(
                "connector_setup_redemption_failed",
                extra={"attempt_id": attempt_id, "error_code": error.code, "http_status": error.http_status},
            )
            if error.code == "connection_substitution" or (not error.retryable and not error.outcome_unknown):
                await self.fail_attempt(attempt_id, code=error.code, claim_owner=owner, claim_generation=generation)
            else:
                # A session is single-use even on errors. Keep reserved; recovery only reads the exact account.
                async with transaction(self._sessions) as session:
                    attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id, with_for_update=True)
                    if attempt is not None and _claim_matches(attempt, owner, generation, now=self._clock()):
                        attempt.last_error_code = error.code
                        attempt.available_at = self._clock() + timedelta(seconds=5)
            if error.retryable or error.outcome_unknown:
                raise ConnectorError(
                    "setup_verification_pending",
                    "Authorization verification is being reconciled. Check the connection status.",
                    category=ErrorCategory.unavailable,
                ) from error
            raise external_error(error) from error
        except ConnectorError as error:
            if error.code != "setup_in_progress":
                await self.fail_attempt(attempt_id, code=error.code, claim_owner=owner, claim_generation=generation)
            raise
        finally:
            with move_on_after(5, shield=True):
                await self.release_attempt(attempt_id, owner=owner, generation=generation)

    async def start_attempt(
        self,
        attempt_id: str,
        *,
        claim_owner: str | None = None,
        claim_generation: int | None = None,
        initial_claim: tuple[str, int] | None = None,
    ) -> SetupStarted:
        if initial_claim is None:
            owner, generation, interrupted = await self._claim_start(attempt_id, claim_owner, claim_generation)
        else:
            owner, generation = initial_claim
            interrupted = False
        try:
            snapshot = await self.attempt_snapshot(attempt_id, claim_owner=owner, claim_generation=generation)
            runtime = configure_provider(self._adapters, snapshot.connector, snapshot.credentials)
            async with aclosing(runtime):
                if interrupted and not runtime.setup_replay_safe:
                    await self.fail_attempt(
                        attempt_id, code="setup_outcome_unknown", claim_owner=owner, claim_generation=generation
                    )
                    raise ConnectorError(
                        "setup_outcome_unknown",
                        "The initial authorization request may have been sent and cannot be repeated.",
                        category=ErrorCategory.conflict,
                    )
                try:
                    try:
                        with fail_after(max(1, self._setup_lease_seconds - 5)):
                            started = await runtime.start_setup(
                                setup=snapshot.attempt.setup_json,
                                context=_setup_context(snapshot.attempt, callback_url=self.callback_url()),
                                resume_ref=snapshot.attempt.setup_ref,
                                before_shared_setup=partial(
                                    reserve_shared_setup,
                                    self._sessions,
                                    provider_id=snapshot.provider_id,
                                    credential_generation=snapshot.credential_generation,
                                    connector_key=snapshot.attempt.connector_key,
                                ),
                            )
                    except TimeoutError as error:
                        raise ConnectorProviderError("setup_outcome_unknown", outcome_unknown=True) from error
                except ConnectorProviderError as error:
                    await self.record_attempt_failure(
                        attempt_id,
                        error,
                        claim_owner=owner,
                        claim_generation=generation,
                        replay_safe=runtime.setup_replay_safe,
                    )
                    if error.outcome_unknown and not runtime.setup_replay_safe:
                        raise ConnectorError(
                            "setup_outcome_unknown",
                            "The authorization request may have succeeded. Start a new authorization explicitly if its link was lost.",
                            category=ErrorCategory.conflict,
                        ) from error
                    raise external_error(error) from error
            try:
                await self._attach_start(attempt_id, started, owner=owner, generation=generation)
            except ConnectorError as error:
                if error.code == "connection_substitution":
                    await self.fail_attempt(attempt_id, code=error.code, claim_owner=owner, claim_generation=generation)
                raise
            return started
        finally:
            # Cancellation leaves `starting` durable; releasing ownership never makes a lost POST safe.
            if claim_owner is None:
                with move_on_after(5, shield=True):
                    await self.release_attempt(attempt_id, owner=owner, generation=generation)

    async def _claim_start(self, attempt_id: str, owner: str | None, generation: int | None) -> tuple[str, int, bool]:
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection, attempt = await _lock_setup(session, attempt_id)
            if attempt is None:
                raise ConnectorError("setup_unavailable", "Setup is unavailable.", category=ErrorCategory.not_found)
            await _require_eligible(session, attempt, connection, now=now)
            if owner is None and generation is None:
                if attempt.status == "pending" and assume_utc(attempt.available_at) > now:
                    raise ConnectorError(
                        "setup_in_progress", "Setup is waiting to retry.", category=ErrorCategory.conflict
                    )
                if attempt.claim_expires_at is not None and assume_utc(attempt.claim_expires_at) > now:
                    raise ConnectorError(
                        "setup_in_progress", "Setup is being processed.", category=ErrorCategory.conflict
                    )
                owner = new_object_id("csa")
                attempt.claim_generation += 1
                generation = attempt.claim_generation
                attempt.claim_owner = owner
                attempt.claim_expires_at = now + timedelta(seconds=self._setup_lease_seconds)
            elif not _claim_matches(attempt, owner, generation, now=now):
                raise ConnectorError("setup_lost_race", "Setup ownership changed.", category=ErrorCategory.conflict)
            if attempt.status == "reserved":
                raise ConnectorError("setup_in_progress", "Setup is completing.", category=ErrorCategory.conflict)
            assert owner is not None and generation is not None
            interrupted = attempt.status == "starting"
            if attempt.status in {"pending", "starting"}:
                attempt.status = "starting"
            attempt.updated_at = now
            return owner, generation, interrupted

    async def _attach_start(self, attempt_id: str, started: SetupStarted, *, owner: str, generation: int) -> None:
        async with transaction(self._sessions) as session:
            connection, attempt = await _lock_setup(session, attempt_id)
            if (
                attempt is None
                or attempt.status not in {"starting", "attached"}
                or not _claim_matches(attempt, owner, generation, now=self._clock())
            ):
                raise ConnectorError(
                    "setup_lost_race", "ConnectorProvider setup changed concurrently.", category=ErrorCategory.conflict
                )
            await _require_eligible(session, attempt, connection, now=self._clock())
            if (
                connection.external_ref is not None
                and started.external_ref is not None
                and connection.external_ref != started.external_ref
            ):
                raise ConnectorError(
                    "connection_substitution",
                    "ConnectorProvider returned another external account.",
                    category=ErrorCategory.conflict,
                )
            now = self._clock()
            connection.updated_at = now
            attempt.external_ref = started.external_ref
            attempt.setup_ref = started.setup_ref
            if started.expires_at is not None:
                attempt.expires_at = min(assume_utc(attempt.expires_at), assume_utc(started.expires_at))
                if assume_utc(attempt.expires_at) <= now:
                    raise ConnectorError(
                        "setup_expired", "Authorization link has expired.", category=ErrorCategory.conflict
                    )
            attempt.completion_method = started.completion_method
            attempt.status = "attached"
            attempt.last_error_code = None
            attempt.available_at = now
            attempt.updated_at = now

    async def release_attempt(self, attempt_id: str, *, owner: str, generation: int) -> None:
        async with transaction(self._sessions) as session:
            attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id, with_for_update=True)
            if attempt is not None and attempt.claim_owner == owner and attempt.claim_generation == generation:
                attempt.claim_owner = None
                attempt.claim_expires_at = None

    async def finish_attempt(
        self,
        attempt_id: str,
        inspection: ConnectionInspection,
        *,
        actor: AuthenticatedActor | None = None,
        claim_owner: str | None = None,
        claim_generation: int | None = None,
    ) -> None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection, attempt = await _lock_setup(session, attempt_id)
            if (
                attempt is None
                or attempt.status not in {"attached", "reserved"}
                or not _claim_matches(attempt, claim_owner, claim_generation, now=self._clock())
            ):
                return
            await _require_eligible(session, attempt, connection, now=now)
            verify_inspection(attempt, connection, inspection)
            if attempt.completion_method != SetupCompletionMethod.polling and attempt.status != "reserved":
                raise ConnectorError(
                    "invalid_callback", "Browser verification is required.", category=ErrorCategory.conflict
                )
            if inspection.status is AdapterConnectionStatus.pending:
                return
            if inspection.status is not AdapterConnectionStatus.ready:
                code = (
                    "incompatible"
                    if inspection.status_reason is AdapterStatusReason.incompatible
                    else "reauthorization_required"
                )
                _fail_setup(connection, attempt, code=code, now=now)
                return
            apply_inspection(connection, inspection, now=now)
            attempt.status = "completed"
            attempt.consumed_at = now
            attempt.reserved_at = None
            attempt.last_error_code = None
            attempt.updated_at = now
            session.add(
                audit(
                    actor or _initiator(attempt),
                    organization_id=connection.organization_id,
                    workspace_id=connection.workspace_id,
                    action="connector_connection.setup.complete",
                    resource_type="connector_connection",
                    resource_id=connection.id,
                    now=now,
                )
            )

        logger.info("connector_setup_completed", extra={"attempt_id": attempt_id})

    async def attempt_snapshot(
        self, attempt_id: str, *, claim_owner: str | None = None, claim_generation: int | None = None
    ) -> AttemptSnapshot:
        async with short_session(self._sessions) as session:
            attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id)
            if attempt is None:
                raise ConnectorError(
                    "setup_unavailable", "ConnectorProvider setup is unavailable.", category=ErrorCategory.not_found
                )
            if claim_owner is not None and not _claim_matches(
                attempt, claim_owner, claim_generation, now=self._clock()
            ):
                raise ConnectorError("setup_lost_race", "Setup ownership changed.", category=ErrorCategory.conflict)
            connection = await require_connection(session, attempt.connector_connection_id)
            await _require_eligible(session, attempt, connection, now=self._clock())
            connector = await require_connector_provider(
                session,
                connection.connector_provider_id,
                scope=ResourceScope(connection.organization_id, connection.workspace_id),
            )
            credential = connector.credential_snapshot()
            provider = ProviderSnapshot.from_record(connector)
            setup = SetupSnapshot(
                attempt.id,
                attempt.generation,
                attempt.connector_key,
                attempt.external_user_correlation,
                attempt.external_ref,
                attempt.setup_ref,
                attempt.completion_method,
                dict(attempt.setup_json),
            )
        try:
            raw = credential.decrypt(self._protector)
        except SecretProtectionError as error:
            raise ConnectorError(
                "credential_unavailable",
                "ConnectorProvider credentials are unavailable.",
                category=ErrorCategory.unavailable,
            ) from error
        return AttemptSnapshot(
            provider_id=connector.id,
            credential_generation=credential.generation,
            attempt=setup,
            connector=provider,
            credentials=decode_credentials(raw),
        )

    async def record_attempt_failure(
        self,
        attempt_id: str,
        error: ConnectorProviderError,
        *,
        replay_safe: bool,
        claim_owner: str | None,
        claim_generation: int | None,
    ) -> None:
        async with transaction(self._sessions) as session:
            connection, attempt = await _lock_setup(session, attempt_id)
            if (
                attempt is None
                or attempt.status not in {"starting", "attached"}
                or not _claim_matches(attempt, claim_owner, claim_generation, now=self._clock())
            ):
                return
            now = self._clock()
            logger.warning(
                "connector_setup_start_failed",
                extra={
                    "attempt_id": attempt_id,
                    "error_code": error.code,
                    "http_status": error.http_status,
                    "outcome_unknown": error.outcome_unknown,
                },
            )
            attempt.attempt_count += 1
            attempt.last_error_code = error.code
            attempt.available_at = now + timedelta(seconds=error.retry_after_seconds or 5)
            attempt.updated_at = now
            if (not error.retryable and not error.outcome_unknown) or (error.outcome_unknown and not replay_safe):
                code = "setup_outcome_unknown" if error.outcome_unknown and not replay_safe else error.code
                _fail_setup(connection, attempt, code=code, now=now)
            elif attempt.status == "starting":
                attempt.status = "pending"

    async def fail_attempt(
        self, attempt_id: str, *, code: str, claim_owner: str | None = None, claim_generation: int | None = None
    ) -> None:
        async with transaction(self._sessions) as session:
            connection, attempt = await _lock_setup(session, attempt_id)
            if attempt is None or attempt.status not in {"pending", "starting", "attached", "reserved"}:
                return
            if claim_owner is not None and not _claim_matches(
                attempt, claim_owner, claim_generation, now=self._clock()
            ):
                return
            _fail_setup(connection, attempt, code=code, now=self._clock())

    def correlation(self, connector: ConnectorProviderRecord, *, workspace_id: str) -> str:
        if self._correlation_secret is None:
            raise ConnectorError(
                "setup_unavailable",
                "ConnectorProvider setup correlation is not configured.",
                category=ErrorCategory.unavailable,
            )
        payload = "\0".join(
            (
                "a13n.connector-user.v1",
                connector.organization_id,
                workspace_id,
                "workspace",
                workspace_id,
                connector.id,
            )
        ).encode()
        digest = hmac.new(self._correlation_secret, payload, hashlib.sha256).digest()
        return "usrh_" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    @staticmethod
    def _verify_callback_proof(attempt: ConnectorSetupAttemptRecord, *, session_uri: str | None) -> None:
        if attempt.completion_method == SetupCompletionMethod.oauth_verifier:
            if session_uri is not None and 1 <= len(session_uri) <= 4096:
                return
            raise ConnectorError(
                "oauth_verifier_required",
                "Configure the provider project's OAuth user verification URL, then restart authorization.",
                category=ErrorCategory.conflict,
            )
        if attempt.completion_method == SetupCompletionMethod.browser_confirmation and session_uri is None:
            # This is the initiating user's confirmation, not proof of the credential-entry browser.
            return
        raise ConnectorError(
            "invalid_callback", "Invalid browser authorization context.", category=ErrorCategory.invalid_request
        )

    def callback_url(self) -> str | None:
        if self._public_origin is None:
            return None
        return f"{self._public_origin}/connector-setup/callback"

    async def _complete_attempt(self, attempt_id: str, *, session_uri: str | None) -> ConnectionInspection:
        snapshot = await self.attempt_snapshot(attempt_id)
        if snapshot.attempt.external_ref is None:
            raise ConnectorProviderError("setup_incomplete")
        require_active_provider(snapshot.connector)
        runtime = configure_provider(self._adapters, snapshot.connector, snapshot.credentials)
        async with aclosing(runtime):
            if snapshot.attempt.completion_method == SetupCompletionMethod.browser_confirmation:
                inspection = await runtime.inspect_setup(
                    setup_ref=snapshot.attempt.external_ref, context=_setup_context(snapshot.attempt, callback_url=None)
                )
                if inspection is None:
                    raise ConnectorProviderError("setup_incomplete")
                return inspection
            if session_uri is None:
                raise ConnectorProviderError("invalid_callback")
            return await runtime.complete_setup(
                session_uri=session_uri,
                context=_setup_context(snapshot.attempt, callback_url=self.callback_url()),
                expected_external_ref=snapshot.attempt.external_ref,
            )


def _fail_setup(
    connection: ConnectorConnectionRecord, attempt: ConnectorSetupAttemptRecord, *, code: str, now: datetime
) -> None:
    attempt.status = "failed"
    attempt.reserved_at = None
    attempt.last_error_code = code
    attempt.updated_at = now
    if connection.setup_generation == attempt.generation and connection.status == "pending":
        connection.status = "action_required"
        connection.status_reason = (
            "incompatible"
            if code
            in {
                "connection_substitution",
                "invalid_provider_response",
                "provider_mismatch",
                "owner_mismatch",
                "incompatible",
            }
            else "reauthorization_required"
        )
        connection.version += 1
        connection.updated_at = now


def _setup_context(attempt: SetupSnapshot, *, callback_url: str | None) -> SetupContext:
    return SetupContext(
        attempt_id=attempt.id,
        generation=attempt.generation,
        connector_key=attempt.connector_key,
        external_user_correlation=attempt.external_user_correlation,
        callback_url=callback_url,
    )


def _claim_matches(
    attempt: ConnectorSetupAttemptRecord,
    claim_owner: str | None,
    claim_generation: int | None,
    *,
    now: datetime,
) -> bool:
    if claim_owner is None or claim_generation is None:
        return claim_owner is None and claim_generation is None and attempt.claim_owner is None
    return (
        attempt.claim_owner == claim_owner
        and attempt.claim_generation == claim_generation
        and attempt.claim_expires_at is not None
        and assume_utc(attempt.claim_expires_at) > now
    )


def _initiator(attempt: ConnectorSetupAttemptRecord) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(
            principal_type=PrincipalType(attempt.initiating_principal_type),
            principal_id=attempt.initiating_principal_id,
        ),
        auth_method="setup",
        credential_id=attempt.id,
        boundary_workspace_id=attempt.workspace_id,
    )


async def _require_eligible(
    session: AsyncSession, attempt: ConnectorSetupAttemptRecord, connection: ConnectorConnectionRecord, *, now: datetime
) -> None:
    initiator = _initiator(attempt)
    await authorize_connection(session, initiator, connection, mode="manage")
    workspace = await session.get(WorkspaceRecord, connection.workspace_id)
    provider = await require_connector_provider(
        session,
        connection.connector_provider_id,
        scope=ResourceScope(connection.organization_id, connection.workspace_id),
    )
    require_active_provider(provider)
    if (
        connection.status != "pending"
        or connection.deleted_at is not None
        or connection.setup_generation != attempt.generation
        or attempt.status not in {"pending", "starting", "attached", "reserved"}
        or assume_utc(attempt.expires_at) <= now
        or workspace is None
        or workspace.deleted_at is not None
    ):
        raise ConnectorError(
            "setup_lost_race", "Connector setup is no longer eligible.", category=ErrorCategory.conflict
        )


async def _lock_setup(
    session: AsyncSession, attempt_id: str
) -> tuple[ConnectorConnectionRecord, ConnectorSetupAttemptRecord | None]:
    connection_id = await session.scalar(
        select(ConnectorSetupAttemptRecord.connector_connection_id).where(ConnectorSetupAttemptRecord.id == attempt_id)
    )
    if connection_id is None:
        raise ConnectorError(
            "setup_unavailable", "ConnectorProvider setup is unavailable.", category=ErrorCategory.not_found
        )
    connection = await require_connection(session, connection_id, lock=True, include_deleted=True)
    attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id, with_for_update=True)
    return connection, attempt
