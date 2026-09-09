"""Ordinary HTTP Run receipts, separate from durable execution and protocol identities."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.digests import digest_request
from a13n_service.durable_operations.idempotency import (
    EvidenceScope,
    IdempotencyConflict,
    IdempotencyIdentity,
    InvalidIdempotencyKey,
    digest_visible_ascii_key,
    load_evidence,
    new_evidence,
)
from a13n_service.durable_operations.requests import request_scope
from a13n_service.environments.selection import Omitted
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.interactions.control_domain import RunAcceptanceReceipt
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .domain import StrictModel
from .errors import (
    InteractionCommandError,
    RunAcceptanceError,
    command_not_found,
    idempotency_conflict,
    map_acceptance_error,
)


def run_command_scope(actor: AuthenticatedActor) -> EvidenceScope:
    # key_digest contains the operation and target as well as the supplied key.
    return request_scope(actor, workspace_id=actor.workspace_id, operation="run.accept", scope_id=actor.workspace_id)


async def load_run_command(
    database: AsyncSession,
    *,
    actor: AuthenticatedActor,
    key: str,
    fingerprint: str,
    now: datetime,
) -> RunAcceptanceReceipt | None:
    evidence = await load_evidence(
        database,
        scope=run_command_scope(actor),
        identity=IdempotencyIdentity(key.removeprefix("idem_"), fingerprint),
        now=now,
    )
    if evidence is None:
        return None
    run = await database.get(RunRecord, evidence.result_ref)
    if run is None or run.organization_id != evidence.organization_id:
        raise RuntimeError("Run command evidence references a missing Run")
    await authorize_agent(
        database,
        actor=actor,
        workspace_id=actor.workspace_id,
        agent_id=run.agent_id,
        action=WorkspaceAction.run_read,
    )
    return RunAcceptanceReceipt.model_validate(evidence.receipt_json)


@dataclass(frozen=True, slots=True)
class RunCommandCommit:
    actor: AuthenticatedActor
    key: str
    fingerprint: str
    now: datetime
    binding: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None

    async def __call__(self, database: AsyncSession, receipt: RunAcceptanceReceipt) -> None:
        """Bind only newly accepted work, then commit its original receipt atomically.

        No external I/O is permitted. Neither hook executes on replay; a failure
        rolls back Run acceptance, protocol bindings, and command evidence.
        """
        if self.binding is not None:
            await self.binding(database, receipt)
        run = await database.get(RunRecord, receipt.run_id)
        if run is None:
            raise RuntimeError("accepted Run is missing")
        database.add(
            new_evidence(
                organization_id=run.organization_id,
                scope=run_command_scope(self.actor),
                identity=IdempotencyIdentity(self.key.removeprefix("idem_"), self.fingerprint),
                result_kind="run_acceptance",
                result_ref=run.id,
                now=self.now,
                receipt=receipt.model_dump(mode="json"),
            )
        )


class RunCommandReceipts:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, clock: Clock = utc_now) -> None:
        self._sessions = sessions
        self._clock = clock

    async def load(
        self,
        *,
        actor: AuthenticatedActor,
        stored_key: str,
        request_fingerprint: str,
    ) -> RunAcceptanceReceipt | None:
        try:
            async with transaction(self._sessions) as database:
                return await load_run_command(
                    database, actor=actor, key=stored_key, fingerprint=request_fingerprint, now=self._clock()
                )
        except IdempotencyConflict as error:
            raise idempotency_conflict() from error
        except AuthorizationError as error:
            raise command_not_found() from error

    async def reconcile(
        self,
        error: RunAcceptanceError,
        *,
        actor: AuthenticatedActor,
        stored_key: str,
        request_fingerprint: str,
    ) -> RunAcceptanceReceipt:
        replay = await self.load(actor=actor, stored_key=stored_key, request_fingerprint=request_fingerprint)
        if replay is not None:
            return replay
        raise map_acceptance_error(error) from error


def require_idempotency_key(value: str) -> None:
    try:
        digest_visible_ascii_key(value)
    except InvalidIdempotencyKey as error:
        raise InteractionCommandError(
            "invalid_request", "Idempotency-Key is invalid.", category=ErrorCategory.invalid_request
        ) from error


def command_identity(idempotency_key: str, request: StrictModel) -> IdempotencyIdentity:
    try:
        return IdempotencyIdentity.from_request(idempotency_key, request)
    except InvalidIdempotencyKey as error:
        raise InteractionCommandError(
            "invalid_request",
            "Idempotency-Key must contain 1 through 512 visible ASCII bytes.",
            category=ErrorCategory.invalid_request,
        ) from error


def scoped_idempotency_key(*, actor: AuthenticatedActor, operation: str, scope_id: str, supplied: str) -> str:
    material = "\x1f".join(
        (
            operation,
            scope_id,
            actor.principal.principal_type.value,
            actor.principal.principal_id,
            supplied,
        )
    ).encode("utf-8")
    return f"idem_{hashlib.sha256(material).hexdigest()}"


def fingerprint_request(request: BaseModel) -> str:
    payload = request.model_dump(mode="json")
    if getattr(request, "environment", Omitted.UNSET) is Omitted.UNSET:
        payload.pop("environment", None)
    return digest_request(payload)
