"""Ordinary HTTP Run receipts, separate from durable execution and protocol identities."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.durable_operations.idempotency import EvidenceScope, IdempotencyIdentity, load_evidence, new_evidence
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_agent
from a13n_service.interactions.control_domain import RunAcceptanceReceipt
from a13n_service.interactions.models import RunRecord


def run_command_scope(actor: AuthenticatedActor) -> EvidenceScope:
    # key_digest contains the operation and target as well as the supplied key.
    return EvidenceScope(
        actor.workspace_id,
        actor.principal.principal_type.value,
        actor.principal.principal_id,
        "run.accept",
        actor.workspace_id,
        organization_id=actor.boundary_organization_id,
    )


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
