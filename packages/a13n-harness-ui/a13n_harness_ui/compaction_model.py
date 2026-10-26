"""Independent compaction model resources and shared foreground admission."""

from __future__ import annotations

from dataclasses import replace
from weakref import WeakValueDictionary

from a13n_harness import HarnessState
from a13n_harness.compaction import CompactionInput, CompactionOperation, CompactionOperator
from a13n_harness.identity import AgentInstanceContext
from a13n_harness.metering import MeteredModel, ModelUsageBinding
from a13n_harness.model_calls import ModelCall
from a13n_harness.pricing import CatalogModelCostCapability, PricingCatalog
from a13n_harness.request_budget import RequestBudget
from a13n_harness.usage import (
    TOKEN_COUNTERS,
    ModelUsageRecord,
    RunUsageLedger,
    UsageSnapshot,
    summarize_usage,
)
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.usage import RunUsage, UsageLimits

from a13n_harness_ui.compaction import BackgroundCompaction
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.storage.usage import ThreadUsageReporter


class CompactionBudget(RequestBudget):
    """One synchronous reservation boundary for foreground and background calls."""

    def __init__(self, limits: UsageLimits | None, registry: WeakValueDictionary[str, CompactionBudget]) -> None:
        self.agent_instance_ids: set[str] = set()
        self.records: dict[str, ModelUsageRecord] = {}
        self.limits = limits
        self.run_id: str | None = None
        self._registry = registry
        super().__init__(limit=limits.request_limit if limits else None)

    def bind(self, run_id: str) -> str:
        if self.run_id is None:
            self.run_id = run_id
            self._registry[run_id] = self
        return self.run_id

    @property
    def used(self) -> int:
        return len(self.records) + self.pending

    def _usage(self) -> RunUsage:
        summary = summarize_usage(self.records.values())
        return RunUsage(
            **{name: getattr(summary, name) for name in TOKEN_COUNTERS},
            requests=self.used,
            cost=summary.cost,
        )

    async def check(self, call: ModelCall) -> RequestBudget | None:
        # Inline children inherit the Host check, but own independent Run budgets.
        if call.agent_instance_id not in self.agent_instance_ids:
            return None
        self.bind(call.harness_run_id)
        if self.limits is not None:
            usage = self._usage()
            self.limits.check_tokens(usage)
            replace(self.limits, request_limit=None).check_before_request(usage)
        self.reserve(call.call_id, continuation=call.continuation_of is not None)
        return self

    def finish(self, call_id: str, record: ModelUsageRecord) -> UsageLimitExceeded | None:
        self.records[record.record_id] = record
        refusal = super().finish(call_id, record)
        if self.limits is not None:
            try:
                self.limits.check_tokens(self._usage())
                self.limits.check_cost(self._usage(), warn_if_cost_unavailable=False)
            except UsageLimitExceeded as error:
                return refusal or error
        return refusal


class CompactionRun(CompactionOperator):
    """Bind detached requests at Run admission, never borrowing an AgentContext."""

    def __init__(
        self, owner: BackgroundCompaction, resolver: HarnessUiModelResolver, catalog: PricingCatalog | None
    ) -> None:
        self.owner = owner
        self._resolver = resolver
        self._cost = CatalogModelCostCapability(catalog=catalog)
        self._scope: tuple[AgentInstanceContext, ThreadUsageReporter, CompactionBudget] | None = None

    async def prepare(
        self,
        *,
        instance: AgentInstanceContext,
        reporter: ThreadUsageReporter,
        state: HarnessState,
        limits: UsageLimits,
        resume_usage: bool = False,
    ) -> CompactionBudget:
        snapshot = UsageSnapshot.from_state(state) if resume_usage else None
        budget = self.owner.budgets.get(snapshot.run_id) if snapshot is not None else None
        if budget is None:
            budget = CompactionBudget(limits, self.owner.budgets)
            if snapshot is not None:
                budget.bind(snapshot.run_id)
                records = await reporter.repository.model_records(
                    thread_id=state.thread_id,
                    run_id=snapshot.run_id,
                    agent_instance_id=snapshot.agent_instance_id,
                )
                budget.records.update((r.record_id, r) for r in records)
                for record in snapshot.records:
                    if isinstance(record, ModelUsageRecord):
                        budget.records.setdefault(record.record_id, record)
        budget.agent_instance_ids.add(instance.agent_instance_id)
        if snapshot is not None:
            budget.agent_instance_ids.add(snapshot.agent_instance_id)
        budget.limits = limits
        budget.limit = limits.request_limit if limits else None
        if snapshot is not None:
            instance = replace(
                instance,
                agent_instance_id=snapshot.agent_instance_id,
                parent_agent_instance_id=snapshot.parent_agent_instance_id,
                delegation_id=snapshot.delegation_id,
            )
        self._scope = (instance, reporter, budget)
        return budget

    async def submit(self, input: CompactionInput) -> CompactionOperation:
        assert self._scope is not None, "Compaction requires Run admission"
        instance, reporter, budget = self._scope
        # Each operation has its own resolver and ledger, but shares Host reservations.
        resolver = self._resolver.fresh()
        run_id = budget.bind(input.run_id)
        ledger = RunUsageLedger(
            run_id=run_id,
            instance=instance,
            thread_id=input.basis.thread_id,
            reporter=reporter,
            snapshot=UsageSnapshot(
                usage_id=input.basis.operation_id,
                thread_id=input.basis.thread_id,
                run_id=run_id,
                agent_instance_id=instance.agent_instance_id,
                parent_agent_instance_id=instance.parent_agent_instance_id,
                delegation_id=instance.delegation_id,
            ),
        )
        binding = ModelUsageBinding(ledger, self._cost, source="compaction", model_call_check=budget)

        async def request(input: CompactionInput) -> ModelResponse:
            if input.model_id is None:
                raise ValueError("Background compaction requires a captured Model recipe")
            model = await resolver.resolve(input.model_id, thread_id=input.basis.thread_id)
            messages, settings, parameters = input.request_messages, input.settings, input.parameters
            context = ModelRequestContext(
                model=model,
                model_id=input.model_id,
                messages=messages,
                model_settings=settings,
                model_request_parameters=parameters,
            )
            async with model:
                return await MeteredModel(context, binding, None).request(messages, settings, parameters)

        return await self.owner.submit(input, request)

    async def inspect(self, thread_id: str) -> CompactionOperation | None:
        return await self.owner.inspect(thread_id)

    async def wait(self, thread_id: str, operation_id: str) -> CompactionOperation:
        return await self.owner.wait(thread_id, operation_id)
