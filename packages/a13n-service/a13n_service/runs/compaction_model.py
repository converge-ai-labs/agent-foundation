"""Worker-owned compaction calls, with independent clients, authority and accounting."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

import anyio
from a13n_harness import HarnessError, RequestContext, RunConfiguration
from a13n_harness.compaction import CompactionInput, CompactionOperation, CompactionOperator, generate_compaction
from a13n_harness.identity import AgentIdentityRef, AgentInstanceContext
from a13n_harness.metering import MeteredModel, ModelUsageBinding
from a13n_harness.model_calls import ModelCall
from a13n_harness.token_pricing import TokenPricingCapability
from a13n_harness.usage import RunUsageLedger, UsageSnapshot
from a13n_logging import exception_details, get_logger
from pydantic_ai.direct import model_request
from pydantic_ai.models import ModelRequestContext

from a13n_service.infra.db import lock, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.models.runtime import open_model
from a13n_service.resources.models.service import ResolvedModel
from a13n_service.resources.models.tables import ModelRow
from a13n_service.resources.providers.service import resolve_provider
from a13n_service.resources.providers.tables import ModelProviderRow
from a13n_service.runs import compactions, request_budget
from a13n_service.runs.admission import CallContext
from a13n_service.runs.agent import _thread_model
from a13n_service.runs.attempts import Lease, LeaseLost, authorize_execution
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.runs.usage import DeltaReporter, UsageBuffer, price_snapshot
from a13n_service.tenancy.authorize import ExecutionAuthority, WorkspaceScope

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Selection:
    model: ResolvedModel
    context: CallContext
    principal_id: str
    request_context: RequestContext
    configuration: RunConfiguration
    limit: int | None


class _CallCheck:
    def __init__(self, runtime: Runtime, owner: compactions.Owner, selection: Selection):
        self.runtime, self.owner, self.selection = runtime, owner, selection
        self.calls: dict[str, ResolvedModel] = {}

    async def check(self, call: ModelCall) -> None:
        selected, runtime = self.selection, self.runtime
        async with transaction(runtime.storage) as session:
            # Same lock order as checkpoint settlement. Sealing or ordinary interruption
            # does not revoke this independent owner; archive and authority changes do.
            thread = await lock(session, ThreadRow, self.owner.thread_id)
            run = await lock(session, RunRow, selected.context.run_id)
            await compactions.lock_owner(session, self.owner)
            if thread is None or thread.archived_at is not None or run is None:
                raise LeaseLost()
            principal = await authorize_execution(session, runtime.access, run)
            model = await session.get(ModelRow, selected.model.id)
            if model is None or not model.enabled:
                raise ServiceError("forbidden", "Compaction model is no longer enabled")
            await resolve_provider(
                session,
                principal,
                ModelProviderRow,
                WorkspaceScope(run.organization_id, run.workspace_id),
                selected.model.provider.id,
                verb="run",
                authority=ExecutionAuthority.model_validate(run.authority),
            )
            await request_budget.reserve(
                session,
                run_id=run.id,
                attempt_id=selected.context.run_attempt_id,
                call_id=call.call_id,
                limit=selected.limit,
                continuation=call.continuation_of is not None,
            )
            if runtime.admission is not None:
                await runtime.admission.proceed(
                    session,
                    replace(
                        selected.context,
                        call_id=call.call_id,
                        source="compaction",
                        model_id=selected.model.id,
                        provider_id=selected.model.provider.id,
                        price_snapshot=price_snapshot(selected.model),
                    ),
                )
        self.calls[call.call_id] = selected.model

    async def reconcile(self) -> None:
        if self.selection.limit is None:
            return
        async with transaction(self.runtime.storage) as session:
            await request_budget.reconcile(session, run_id=self.selection.context.run_id, limit=self.selection.limit)


class BackgroundCompaction:
    """Only live generation tasks are retained; durable observations are always read from storage."""

    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self.running: dict[str, asyncio.Task[None]] = {}

    async def submit(self, lease: Lease, input: CompactionInput, selection: Selection) -> CompactionOperation:
        if self.runtime.tasks.stopping:
            raise LeaseLost()
        if input.model_id != selection.model.key:
            raise ValueError("Compaction did not select the captured root model")
        owner = await compactions.reserve(self.runtime, lease, input)
        if owner is not None:
            try:
                await compactions.publish(self.runtime, owner, input)
                task = self.runtime.tasks.start(
                    self._generate(owner, input, selection), name=f"compaction-{owner.operation_id}"
                )
                self.running[owner.operation_id] = task
                task.add_done_callback(lambda _: self.running.pop(owner.operation_id, None))
            except BaseException:
                with anyio.CancelScope(shield=True):
                    await compactions.fail(self.runtime, owner, "compaction_interrupted")
                raise
        operation = await self.inspect(lease.thread_id)
        if operation is None:
            raise LeaseLost()
        return operation

    async def inspect(self, thread_id: str) -> CompactionOperation | None:
        observed = await compactions.observe(self.runtime, thread_id)
        return await compactions.load(self.runtime, observed) if observed is not None else None

    async def wait(self, thread_id: str, operation_id: str) -> CompactionOperation:
        while True:
            operation = await self.inspect(thread_id)
            if operation is None or operation.input.basis.operation_id != operation_id:
                raise LeaseLost()
            if operation.status != "running":
                return operation
            # Poll durable state; cancelling a waiter cannot propagate to generation.
            await asyncio.sleep(min(0.25, self.runtime.settings.worker.authority_seconds))

    async def _heartbeat(self, owner: compactions.Owner) -> None:
        while True:
            await asyncio.sleep(
                min(self.runtime.settings.worker.authority_seconds, self.runtime.settings.worker.lease_seconds / 3)
            )
            await compactions.renew(self.runtime, owner)

    async def _generate(self, owner: compactions.Owner, input: CompactionInput, selection: Selection) -> None:
        runtime, selected = self.runtime, selection.model
        check = _CallCheck(runtime, owner, selection)
        reporter = DeltaReporter(
            runtime.storage,
            selection.context.run_id,
            selection.context.run_attempt_id,
            UsageBuffer(check.calls),
            check=check.reconcile,
        )
        instance = AgentInstanceContext(
            identity=AgentIdentityRef(issuer="a13n-service", subject=selection.principal_id),
            agent_instance_id=selection.context.run_id,
        )
        ledger = RunUsageLedger(
            run_id=input.run_id,
            instance=instance,
            thread_id=input.basis.thread_id,
            reporter=reporter,
            snapshot=UsageSnapshot(
                usage_id=input.basis.operation_id,
                thread_id=input.basis.thread_id,
                run_id=input.run_id,
                agent_instance_id=instance.agent_instance_id,
            ),
        )
        binding = ModelUsageBinding(
            ledger,
            TokenPricingCapability({selected.key: selected.pricing} if selected.pricing else {}),
            source="compaction",
            model_call_check=check,
        )
        try:
            async with asyncio.TaskGroup() as group:
                heartbeat = group.create_task(self._heartbeat(owner))
                try:
                    async with open_model(
                        selected,
                        registry=runtime.registry,
                        keys=runtime.keys,
                        policy=runtime.endpoint_policy.for_run(selection.configuration),
                        settings=runtime.settings.providers,
                        storage=runtime.storage,
                        request_context=selection.request_context,
                    ) as native:
                        model = _thread_model(selected, native, owner.thread_id)

                        async def request(input: CompactionInput):
                            messages, settings, parameters = input.request_messages, input.settings, input.parameters
                            messages = model.prepare_messages(messages, parameters)
                            context = ModelRequestContext(
                                model=model,
                                model_id=selected.key,
                                messages=messages,
                                model_settings=settings,
                                model_request_parameters=parameters,
                            )
                            return await model_request(
                                MeteredModel(context, binding, None),
                                messages,
                                model_settings=settings,
                                model_request_parameters=parameters,
                                instrument=False,
                            )

                        result = await generate_compaction(input, request)
                    # Retry publication only; never repeat generation after a storage failure.
                    for attempt in range(3):
                        try:
                            await compactions.publish(runtime, owner, result)
                            break
                        except LeaseLost:
                            raise
                        except Exception:
                            if attempt == 2:
                                raise
                            await asyncio.sleep(0.1 * 2**attempt)
                finally:
                    heartbeat.cancel()
        except asyncio.CancelledError:
            with anyio.CancelScope(shield=True):
                await compactions.fail(runtime, owner, "compaction_interrupted")
            raise
        except Exception as error:
            cause = error
            while isinstance(cause, ExceptionGroup) and len(cause.exceptions) == 1:
                cause = cause.exceptions[0]
            code = cause.code if isinstance(cause, HarnessError) else "compaction_failed"
            logger.warning(
                "Compaction failed",
                extra={"operation_id": owner.operation_id, "exception_details": exception_details(error)},
            )
            await compactions.fail(runtime, owner, code)
        finally:
            # This reporter owns its pending queue; a sealed parent cannot close or overwrite it.
            with anyio.CancelScope(shield=True):
                with anyio.move_on_after(runtime.settings.worker.drain_seconds):
                    await reporter.flush()


class CompactionRun(CompactionOperator):
    def __init__(self, owner: BackgroundCompaction, lease: Lease, selection: Selection):
        self.owner, self.lease, self.selection = owner, lease, selection
        self._declined: CompactionOperation | None = None

    async def submit(self, input: CompactionInput) -> CompactionOperation:
        if len(input.model_dump_json().encode()) > self.owner.runtime.settings.objects.max_bytes:
            # No generation was admitted. Keep the observation for this Run so Harness
            # uses its once-per-failure synchronous fallback without reserving durable work.
            self._declined = CompactionOperation(input=input, status="failed", error_code="payload_too_large")
            return self._declined
        return await self.owner.submit(self.lease, input, self.selection)

    async def inspect(self, thread_id: str) -> CompactionOperation | None:
        if thread_id != self.lease.thread_id:
            raise ValueError("Compaction operator is bound to one Thread")
        return self._declined or await self.owner.inspect(thread_id)

    async def wait(self, thread_id: str, operation_id: str) -> CompactionOperation:
        if thread_id != self.lease.thread_id:
            raise ValueError("Compaction operator is bound to one Thread")
        if self._declined is not None and self._declined.input.basis.operation_id == operation_id:
            return self._declined
        return await self.owner.wait(thread_id, operation_id)
