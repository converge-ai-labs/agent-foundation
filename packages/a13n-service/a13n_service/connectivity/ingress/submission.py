"""Accept provider batches through the canonical Run and Steer commands."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import AgentRunOverride
from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.reception import InputOverride
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.connectivity.native_context import InboundRunContext
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    PrincipalRef,
    PrincipalType,
    WorkspaceAction,
    authorize_agent,
    authorize_workspace,
)
from a13n_service.interactions.command_values import ContinueRunCommand, StartRunCommand
from a13n_service.interactions.commands import InteractionCommands
from a13n_service.interactions.control_domain import RunAcceptanceReceipt, SteerReceipt
from a13n_service.interactions.errors import InteractionCommandError
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.origin import SubmissionOrigin
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, assume_utc, utc_now

from .admission_domain import (
    AcceptedInputOutcome,
    InputAcceptanceOutcome,
    LostRaceInputOutcome,
    PreparedIngressBatch,
    RejectedInputOutcome,
)
from .admission_models import AgentThreadBindingRecord, IngressBatchRecord


@dataclass(frozen=True, slots=True)
class _Selection:
    actor: AuthenticatedActor
    agent_id: str
    override: AgentRunOverride | None
    account_version: int
    target_id: str | None
    target_version: int | None
    thread_id: str | None
    thread_version: int | None
    run_id: str | None
    steer: bool


class IngressInputAcceptor:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], commands: InteractionCommands, *, clock: Clock = utc_now
    ) -> None:
        self._sessions = sessions
        self._commands = commands
        self._clock = clock

    async def accept_ingress_batch(self, batch: PreparedIngressBatch) -> InputAcceptanceOutcome:
        try:
            async with short_session(self._sessions) as session:
                stored = await session.get(IngressBatchRecord, batch.batch_id)
                if stored is None:
                    return RejectedInputOutcome(reason_code="batch_unavailable")
                # The receipt is authoritative even after config or IAM changes.
                if stored.status == "accepted":
                    if stored.result_kind not in {"run", "steer"} or stored.result_id is None:
                        raise ValueError("batch_receipt_invalid")
                    return AcceptedInputOutcome(
                        receipt_kind="run" if stored.result_kind == "run" else "steer", receipt_id=stored.result_id
                    )
                selection = await self._selection(session, batch)

            async def commit(session: AsyncSession, receipt: RunAcceptanceReceipt | SteerReceipt) -> None:
                current = await self._selection(session, batch, lock=True)
                if (current.account_version, current.target_id, current.target_version) != (
                    selection.account_version,
                    selection.target_id,
                    selection.target_version,
                ):
                    raise _LostClaim()
                binding = await session.scalar(
                    select(AgentThreadBindingRecord)
                    .where(AgentThreadBindingRecord.id == batch.binding_id)
                    .with_for_update()
                )
                stored = await session.scalar(
                    select(IngressBatchRecord).where(IngressBatchRecord.id == batch.batch_id).with_for_update()
                )
                now = self._clock()
                if (
                    binding is None
                    or stored is None
                    or stored.status != "pending"
                    or stored.claim_owner != batch.claim_owner
                    or stored.claim_generation != batch.claim_generation
                    or stored.claim_expires_at is None
                    or assume_utc(stored.claim_expires_at) <= now
                ):
                    raise _LostClaim()
                if binding.thread_id is not None and binding.thread_id != receipt.thread_id:
                    raise _LostClaim()
                binding.thread_id = receipt.thread_id
                binding.next_submission_at = now + timedelta(
                    milliseconds=batch.configuration.input_batching.min_interval_ms
                )
                binding.updated_at = now
                stored.status = "accepted"
                stored.result_kind = "steer" if isinstance(receipt, SteerReceipt) else "run"
                stored.result_id = receipt.steer_id if isinstance(receipt, SteerReceipt) else receipt.run_id
                stored.terminal_at = now
                stored.updated_at = now
                stored.claim_owner = None
                stored.claim_expires_at = None

            key = batch.batch_id
            if selection.steer:
                assert selection.run_id is not None
                receipt = await self._commands.active.steer(
                    actor=selection.actor,
                    run_id=selection.run_id,
                    idempotency_key=key,
                    input=batch.agent_input,
                    transaction_hook=commit,
                )
                return AcceptedInputOutcome(receipt_kind="steer", receipt_id=receipt.steer_id)
            origin = SubmissionOrigin(
                trigger_type="inbound",
                native_tool_contexts=(
                    InboundRunContext.from_batch(batch, execution_principal=selection.actor.principal).model_dump(
                        mode="json"
                    ),
                ),
            )
            if selection.thread_id is None:
                run_receipt = await self._commands.runs.start(
                    actor=selection.actor,
                    workspace_id=batch.workspace_id,
                    idempotency_key=key,
                    request=StartRunCommand(
                        agent_id=selection.agent_id, input=batch.agent_input, config_override=selection.override
                    ),
                    origin=origin,
                    transaction_hook=commit,
                )
            else:
                assert selection.thread_version is not None
                request = ContinueRunCommand(
                    expected_thread_version=selection.thread_version,
                    agent_id=selection.agent_id,
                    input=batch.agent_input,
                    config_override=selection.override,
                )
                if selection.run_id is None:
                    run_receipt = await self._commands.runs.continue_empty_thread(
                        actor=selection.actor,
                        thread_id=selection.thread_id,
                        idempotency_key=key,
                        request=request,
                        origin=origin,
                        transaction_hook=commit,
                    )
                else:
                    run_receipt = await self._commands.runs.continue_from(
                        actor=selection.actor,
                        source_run_id=selection.run_id,
                        idempotency_key=key,
                        request=request,
                        origin=origin,
                        transaction_hook=commit,
                    )
            return AcceptedInputOutcome(receipt_kind="run", receipt_id=run_receipt.run_id)
        except _LostClaim:
            return LostRaceInputOutcome()
        except AuthorizationError:
            return RejectedInputOutcome(reason_code="execution_principal_unauthorized")
        except _Ineligible as error:
            return RejectedInputOutcome(reason_code=str(error))
        except InteractionCommandError as error:
            if error.category is ErrorCategory.conflict:
                return LostRaceInputOutcome()
            if error.category not in {
                ErrorCategory.internal,
                ErrorCategory.dependency_failure,
                ErrorCategory.unavailable,
                ErrorCategory.timeout,
            }:
                return RejectedInputOutcome(reason_code=error.code)
            raise

    async def _selection(self, session: AsyncSession, batch: PreparedIngressBatch, *, lock: bool = False) -> _Selection:
        query = select(AccountRecord).where(
            AccountRecord.id == batch.configuration.account_id,
            AccountRecord.organization_id == batch.organization_id,
            AccountRecord.workspace_id == batch.workspace_id,
        )
        account = await session.scalar(query.with_for_update() if lock else query)
        if (
            account is None
            or account.status != "active"
            or account.deleted_at is not None
            or account.default_agent_id is None
            or account.execution_service_account_id is None
        ):
            raise _Ineligible("account_inactive")
        target_query = select(AccountTargetRecord).where(
            AccountTargetRecord.account_id == account.id,
            AccountTargetRecord.target_kind == batch.configuration.target_kind,
            AccountTargetRecord.external_target_id == batch.configuration.external_target_id,
        )
        target = await session.scalar(target_query.with_for_update() if lock else target_query)
        actor = AuthenticatedActor(
            principal=PrincipalRef(
                principal_type=PrincipalType.service_account, principal_id=account.execution_service_account_id
            ),
            auth_method="internal",
            credential_id="connectivity-admission",
            boundary_workspace_id=account.workspace_id,
        )
        await authorize_workspace(
            session, actor=actor, workspace_id=batch.workspace_id, action=WorkspaceAction.application_account_use
        )
        agent_id = target.agent_id if target is not None and target.agent_id is not None else account.default_agent_id
        override = (
            InputOverride.model_validate(target.config_override_json).invocation_override()
            if target is not None and target.config_override_json is not None
            else None
        )
        binding = await session.get(AgentThreadBindingRecord, batch.binding_id)
        if binding is None or binding.account_id != account.id:
            raise _Ineligible("binding_unavailable")
        thread = await session.get(ThreadRecord, binding.thread_id) if binding.thread_id is not None else None
        if binding.thread_id is not None and (thread is None or thread.organization_id != batch.organization_id):
            raise _Ineligible("thread_unavailable")
        run_id = (thread.current_run_id or thread.head_run_id) if thread is not None else None
        run = await session.get(RunRecord, run_id) if run_id is not None else None
        steer = run is not None and run.status in {"accepted", "running", "waiting"}
        await authorize_agent(
            session,
            actor=actor,
            workspace_id=batch.workspace_id,
            agent_id=run.agent_id if steer and run is not None else agent_id,
            action=WorkspaceAction.agent_invoke,
        )
        return _Selection(
            actor,
            agent_id,
            override,
            account.version,
            target.id if target else None,
            target.version if target else None,
            binding.thread_id,
            thread.version if thread else None,
            run_id,
            steer,
        )


class _LostClaim(Exception):
    pass


class _Ineligible(Exception):
    pass
