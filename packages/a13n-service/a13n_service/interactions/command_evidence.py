"""HTTP request keys and current Run projections."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.durable_operations.entity_keys import find_by_key, scope_key
from a13n_service.durable_operations.idempotency import (
    EvidenceScope,
    IdempotencyIdentity,
    InvalidIdempotencyKey,
    digest_visible_ascii_key,
)
from a13n_service.durable_operations.requests import request_scope
from a13n_service.hooks.persistence import load_inline_hook_subscription
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction
from a13n_service.interactions.control_domain import RunAcceptanceReceipt
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.observability.correlation import publish_run_acceptance
from a13n_service.storage import short_session

from .access import authorize_interaction
from .errors import (
    InteractionCommandError,
    RunAcceptanceError,
    command_not_found,
    map_acceptance_error,
)


def run_command_scope(actor: AuthenticatedActor) -> EvidenceScope:
    # key_digest contains the operation and target as well as the supplied key.
    return request_scope(actor, workspace_id=actor.workspace_id, operation="run.accept", scope_id=actor.workspace_id)


class RunRequest:
    """One scoped HTTP request key carried by its accepted Run."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        actor: AuthenticatedActor,
        operation: str,
        scope_id: str,
        supplied_key: str,
        binding: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
    ) -> None:
        require_idempotency_key(supplied_key)
        key = scoped_idempotency_key(actor=actor, operation=operation, scope_id=scope_id, supplied=supplied_key)
        self.key = scope_key(run_command_scope(actor), key.removeprefix("idem_"))
        self._sessions = sessions
        self._actor = actor
        self._binding = binding

    async def replay(self) -> RunAcceptanceReceipt | None:
        try:
            async with short_session(self._sessions) as database:
                run = await find_by_key(database, RunRecord, self.key)
                if run is None:
                    return None
                await authorize_interaction(
                    database,
                    actor=self._actor,
                    workspace_id=self._actor.workspace_id,
                    session_id=run.session_id,
                    agent_id=run.agent_id,
                    action=WorkspaceAction.run_read,
                )
                receipt = await run_receipt(database, run)
            publish_run_acceptance(receipt.run_id)
            return receipt
        except AuthorizationError as error:
            raise command_not_found() from error

    async def commit(self, database: AsyncSession, receipt: RunAcceptanceReceipt) -> None:
        if self._binding is not None:
            await self._binding(database, receipt)

    async def reconcile(self, error: RunAcceptanceError) -> RunAcceptanceReceipt:
        replay = await self.replay()
        if replay is not None:
            return replay
        raise map_acceptance_error(error) from error


async def run_receipt(database: AsyncSession, run: RunRecord) -> RunAcceptanceReceipt:
    thread = await database.get(ThreadRecord, run.thread_id)
    if thread is None:
        raise command_not_found()
    hook = await load_inline_hook_subscription(database, organization_id=run.organization_id, run_id=run.id)
    return RunAcceptanceReceipt(
        session_id=run.session_id,
        thread_id=run.thread_id,
        thread_version=thread.version,
        run_id=run.id,
        run_version=run.version,
        hook_subscription_id=None if hook is None else hook[0].id,
    )


def require_idempotency_key(value: str) -> None:
    try:
        digest_visible_ascii_key(value)
    except InvalidIdempotencyKey as error:
        raise InteractionCommandError(
            "invalid_request", "Idempotency-Key is invalid.", category=ErrorCategory.invalid_request
        ) from error


def command_identity(idempotency_key: str) -> IdempotencyIdentity:
    try:
        return IdempotencyIdentity.from_key(idempotency_key)
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
