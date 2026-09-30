"""Canonical run-local usage facts, summaries and optional Host delivery."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, runtime_checkable

import anyio
from a13n_logging import get_logger
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, WrapModelRequestHandler
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.usage import RequestUsage, RunUsage, UsageLimits

from a13n_harness._json import dump_json_bytes, is_sensitive_key
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError, RunError, StateError
from a13n_harness.identity import AgentInstanceContext
from a13n_harness.money import sum_decimal
from a13n_harness.pricing import MODEL_COST_CAPABILITY_ID, AbstractModelCostCapability
from a13n_harness.providers.usage import ProviderUsage as ProviderUsage
from a13n_harness.providers.usage import UsageMeasure as UsageMeasure
from a13n_harness.request_budget import RequestBudget
from a13n_harness.state import AgentContextState, AgentContextStateSnapshot, CapabilityState, HarnessState

if TYPE_CHECKING:
    from a13n_harness.events import HarnessEventEmitter

logger = get_logger(__name__)

USAGE_CAPABILITY_ID = "a13n.usage"
_USAGE_CLEANUP_SECONDS = 5
_MAX_RECORDS = 10_000
_MAX_REPORT_BYTES = 56 * 1024
_MAX_REPORT_RECORDS = 64
_MAX_USAGE_DETAILS = 64
_MAX_COUNTER = 2**63 - 1

type CostSource = Literal[
    "catalog",
    "custom",
    "provider_or_genai_prices",
    "unknown",
]
type PricingStatus = Literal[
    "applied",
    "declined",
    "failed",
    "disabled",
    "not_reached",
]
type UsageReportReason = Literal["model_request", "provider", "terminal"]


_LIMIT_FIELDS = (
    "cost_limit",
    "request_limit",
    "tool_calls_limit",
    "input_tokens_limit",
    "output_tokens_limit",
    "total_tokens_limit",
    "per_request_input_tokens_limit",
)


def intersect_usage_limits(*values: UsageLimits | None) -> UsageLimits | None:
    """Return fresh native limits no broader than any supplied ceiling."""
    present = tuple(value for value in values if value is not None)
    if not present:
        return None
    fields: dict[str, Any] = {}
    for name in _LIMIT_FIELDS:
        ceilings = [getattr(value, name) for value in present if getattr(value, name) is not None]
        fields[name] = min(ceilings) if ceilings else None
    fields["count_tokens_before_request"] = any(value.count_tokens_before_request for value in present)
    return UsageLimits(**fields)


class UsageCounters(BaseModel):
    """Safe fixed-shape projection of one Pydantic request usage value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    cache_write_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    cache_read_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    output_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    input_audio_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    cache_audio_read_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    output_audio_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    audio_seconds: Decimal = Field(default=Decimal(0), ge=0, allow_inf_nan=False)
    cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)


class BoundedRequestUsage(UsageCounters):
    """One request's counters and bounded provider detail, with USD cost."""

    details: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_projection(self) -> BoundedRequestUsage:
        if len(self.details) > _MAX_USAGE_DETAILS:
            raise ValueError("request usage has too many detail counters")
        for key, value in self.details.items():
            if not key or len(key) > 128 or "\x00" in key or not 0 <= value <= _MAX_COUNTER:
                raise ValueError("request usage detail is invalid")
        if self.cost is not None and (not self.cost.is_finite() or self.cost < 0):
            raise ValueError("request usage cost is invalid")
        return self

    @classmethod
    def from_request_usage(cls, usage: RequestUsage) -> BoundedRequestUsage:
        """Copy supported counters and omit unsafe provider detail instead of leaking it."""
        details = {
            key: value
            for key, value in sorted(usage.details.items())
            if isinstance(key, str)
            and bool(key)
            and len(key) <= 128
            and "\x00" not in key
            and not is_sensitive_key(key)
            and isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= _MAX_COUNTER
        }
        return cls(
            input_tokens=usage.input_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            output_tokens=usage.output_tokens,
            input_audio_tokens=usage.input_audio_tokens,
            cache_audio_read_tokens=usage.cache_audio_read_tokens,
            output_audio_tokens=usage.output_audio_tokens,
            audio_seconds=Decimal(str(usage.audio_seconds)),
            details=dict(tuple(details.items())[:_MAX_USAGE_DETAILS]),
            cost=usage.cost,
        )


class ModelUsageObservation(BaseModel):
    """Fields a later observation of the same generation can refine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    usage_status: Literal["complete", "partial", "unavailable"] = "complete"
    outcome: Literal["completed", "failed", "cancelled"] = "completed"
    response_state: str = Field(min_length=1, max_length=64)
    model_name: str | None = Field(default=None, max_length=1024)
    provider_name: str | None = Field(default=None, max_length=512)
    response_timestamp: datetime
    request_usage: BoundedRequestUsage
    pricing_revision: str | None = Field(default=None, max_length=256)
    pricing_rule_id: str | None = Field(default=None, max_length=128)
    cost_source: CostSource = "unknown"
    pricing_status: PricingStatus = "not_reached"


class ModelUsageRecord(ModelUsageObservation):
    """One observed generation: immutable attribution plus a revisable observation."""

    kind: Literal["model"] = "model"
    record_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=256)
    response_ordinal: int = Field(ge=0)
    model_id: str | None = Field(default=None, max_length=1024)
    provider_response_id: str | None = Field(default=None, max_length=1024)
    call_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_run_id: str | None = Field(default=None, max_length=256)
    agent_instance_id: str = Field(min_length=1, max_length=512)
    parent_agent_instance_id: str | None = Field(default=None, max_length=512)
    delegation_id: str | None = Field(default=None, max_length=512)
    request_started_at: datetime | None = None
    source: str = Field(default="agent", min_length=1, max_length=256)
    tool_id: str | None = Field(default=None, max_length=256)
    tool_call_id: str | None = Field(default=None, max_length=1024)


class ProviderUsageRecord(BaseModel):
    """One deduplicated non-model usage contribution with run and tool attribution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["provider"] = "provider"
    record_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=256)
    ordinal: int = Field(ge=0)
    agent_instance_id: str = Field(min_length=1, max_length=512)
    parent_agent_instance_id: str | None = Field(default=None, max_length=512)
    delegation_id: str | None = Field(default=None, max_length=512)
    source: str = Field(min_length=1, max_length=256)
    tool_id: str | None = Field(default=None, max_length=256)
    tool_call_id: str | None = Field(default=None, max_length=1024)
    usage: ProviderUsage

    @field_validator("source", "tool_id", "tool_call_id")
    @classmethod
    def _validate_text(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("usage attribution text must not contain NUL")
        return value


type UsageRecord = ModelUsageRecord | ProviderUsageRecord


def update_observation(previous: ModelUsageRecord, observation: ModelUsageObservation) -> ModelUsageRecord:
    """Refine a cumulative generation without rolling back a newer accounting checkpoint."""
    if any(
        getattr(observation.request_usage, name) < getattr(previous.request_usage, name)
        for name in (*TOKEN_COUNTERS, "audio_seconds")
    ) or (previous.response_state == "complete" and observation.response_state != "complete"):
        return previous.model_copy(deep=True)
    if previous.pricing_revision is not None and observation.pricing_revision != previous.pricing_revision:
        observation = observation.model_copy(
            update={
                "request_usage": observation.request_usage.model_copy(update={"cost": None}),
                "pricing_revision": previous.pricing_revision,
                "pricing_rule_id": previous.pricing_rule_id,
                "pricing_status": "declined",
                "cost_source": "unknown",
            }
        )
    return previous.model_copy(
        update={
            **{name: getattr(observation, name) for name in ModelUsageObservation.model_fields},
        },
        deep=True,
    )


def validate_contribution(previous: UsageRecord, record: UsageRecord) -> None:
    """Keep a contribution's identity and ownership fixed while observing its generation."""
    mutable = set(ModelUsageObservation.model_fields) if isinstance(previous, ModelUsageRecord) else set()
    if previous.model_dump(exclude=mutable) != record.model_dump(exclude=mutable):
        raise RunError("Usage contribution changed its attribution.", code="usage_record_conflict")
    if isinstance(previous, ModelUsageRecord) and isinstance(record, ModelUsageRecord):
        if (
            previous.pricing_revision is not None
            and previous.pricing_revision != record.pricing_revision
            and record.request_usage.cost is not None
        ):
            raise RunError("Usage contribution changed its price policy.", code="usage_record_conflict")


class RunUsageSummary(BoundedRequestUsage):
    """Known run-local totals. Unknown and partial records remain explicit."""

    requests: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    provider_receipts: int = Field(default=0, ge=0)
    unknown_cost_records: int = Field(default=0, ge=0)
    incomplete_requests: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cache_hit_rate(self) -> float | None:
        return self.cache_read_tokens / self.input_tokens if self.input_tokens else None


TOKEN_COUNTERS = (
    "input_tokens",
    "cache_write_tokens",
    "cache_read_tokens",
    "output_tokens",
    "input_audio_tokens",
    "cache_audio_read_tokens",
    "output_audio_tokens",
)


@dataclass
class UsageAccumulator:
    """Incremental totals over distinct latest records; callers own selection and grouping."""

    requests: int = 0
    receipts: int = 0
    tokens: dict[str, int] = field(default_factory=lambda: dict.fromkeys(TOKEN_COUNTERS, 0))
    details: dict[str, int] = field(default_factory=dict)
    cost: Decimal = Decimal(0)
    unknown: int = 0
    provider_cost: Decimal = Decimal(0)
    unknown_provider: int = 0
    audio_seconds: Decimal = Decimal(0)
    incomplete: int = 0

    def add(self, record: UsageRecord) -> None:
        if isinstance(record, ModelUsageRecord):
            self.requests += 1
            usage = record.request_usage
            for name in TOKEN_COUNTERS:
                self.tokens[name] += getattr(usage, name)
            for name, count in usage.details.items():
                self.details[name] = self.details.get(name, 0) + count
                if len(self.details) > _MAX_USAGE_DETAILS:
                    del self.details[max(self.details)]
            self.audio_seconds = sum_decimal((self.audio_seconds, usage.audio_seconds))
            self.incomplete += record.usage_status != "complete"
            if usage.cost is None:
                self.unknown += 1
            else:
                self.cost = sum_decimal((self.cost, usage.cost))
        else:
            self.receipts += 1
            if record.usage.cost is None or record.usage.currency != "USD":
                self.unknown_provider += 1
            else:
                self.provider_cost = sum_decimal((self.provider_cost, record.usage.cost))

    @property
    def model_cost(self) -> Decimal | None:
        return self.cost if self.requests > self.unknown else None

    @property
    def receipt_cost(self) -> Decimal | None:
        return self.provider_cost if self.receipts > self.unknown_provider else None

    def summary(self, *, tool_calls: int = 0) -> RunUsageSummary:
        known = self.requests + self.receipts > self.unknown + self.unknown_provider
        return RunUsageSummary(
            **self.tokens,
            details=dict(sorted(self.details.items())),
            audio_seconds=self.audio_seconds,
            requests=self.requests,
            tool_calls=tool_calls,
            provider_receipts=self.receipts,
            cost=sum_decimal((self.cost, self.provider_cost)) if known else None,
            unknown_cost_records=self.unknown + self.unknown_provider,
            incomplete_requests=self.incomplete,
        )


def summarize_usage(records: Iterable[UsageRecord], *, tool_calls: int = 0) -> RunUsageSummary:
    """Sum distinct current contributions; repeated provider receipts retain their first owner."""
    latest: dict[str, UsageRecord] = {}
    for record in records:
        previous = latest.get(record.record_id)
        if previous is not None:
            if isinstance(previous, ProviderUsageRecord) and isinstance(record, ProviderUsageRecord):
                if previous.usage == record.usage:
                    continue
            if previous != record:
                raise RunError("Conflicting current usage contributions.", code="usage_record_conflict")
        latest[record.record_id] = record
    totals = UsageAccumulator()
    for record in latest.values():
        totals.add(record)
    return totals.summary(tool_calls=tool_calls)


class UsageScope(BaseModel):
    """Bounded identity and progress of one single-writer accounting scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    usage_id: str = Field(min_length=1, max_length=128)
    thread_id: str | None = Field(default=None, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    agent_instance_id: str = Field(min_length=1, max_length=512)
    parent_agent_instance_id: str | None = Field(default=None, max_length=512)
    delegation_id: str | None = Field(default=None, max_length=512)
    sequence: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)


def _validate_records(scope: UsageScope, records: tuple[UsageRecord, ...]) -> None:
    ids: set[str] = set()
    for record in records:
        if record.record_id in ids or (
            record.run_id,
            record.agent_instance_id,
            record.parent_agent_instance_id,
            record.delegation_id,
        ) != (scope.run_id, scope.agent_instance_id, scope.parent_agent_instance_id, scope.delegation_id):
            raise ValueError("Usage contributions must have unique identities and one owner")
        ids.add(record.record_id)


class UsageSnapshot(UsageScope):
    """Current accounting state for one single-writer scope, independent of execution checkpoints."""

    kind: Literal["snapshot"] = "snapshot"
    records: tuple[Annotated[UsageRecord, Field(discriminator="kind")], ...] = Field(
        default=(), max_length=_MAX_RECORDS
    )

    @model_validator(mode="after")
    def _validate_scope(self) -> UsageSnapshot:
        _validate_records(self, self.records)
        if len(dump_json_bytes(self.model_dump(mode="json"))) > 16 * 1024 * 1024:
            raise ValueError("Usage snapshot exceeds its 16 MiB bound")
        return self

    @property
    def scope(self) -> UsageScope:
        return UsageScope(**{name: getattr(self, name) for name in UsageScope.model_fields})

    @property
    def summary(self) -> RunUsageSummary:
        return summarize_usage(self.records, tool_calls=self.tool_calls)

    @classmethod
    def from_state(cls, state: HarnessState) -> UsageSnapshot | None:
        entry = state.agent_context_state.entries.get(USAGE_CAPABILITY_ID)
        if entry is None:
            return None
        if entry.version != "1":
            raise StateError("Unsupported usage state version.", code="usage_state_invalid")
        snapshot = cls.model_validate(entry.data)
        if snapshot.thread_id != state.thread_id:
            raise StateError("Usage state belongs to another Thread.", code="usage_state_mismatch")
        return snapshot

    def restore(self, state: HarnessState) -> HarnessState:
        """Overlay latest matching accounting without changing the selected execution history."""
        previous = self.from_state(state)
        if previous is None:
            raise StateError("Accounting resume requires usage state.", code="usage_state_missing")
        selected = select_usage_snapshot(previous, self)
        entries = state.agent_context_state.entries
        entries[USAGE_CAPABILITY_ID] = CapabilityState(version="1", data=selected.model_dump(mode="json"))
        return state.model_copy(update={"agent_context_state": AgentContextStateSnapshot(entries=entries)})


def select_usage_snapshot(previous: UsageSnapshot, current: UsageSnapshot) -> UsageSnapshot:
    """Choose current state from one owner; a sequence never merges independent writers."""
    identity = {"usage_id", "thread_id", "run_id", "agent_instance_id", "parent_agent_instance_id", "delegation_id"}
    if previous.model_dump(include=identity) != current.model_dump(include=identity):
        raise StateError("Usage snapshot owner does not match.", code="usage_state_mismatch")
    if previous.sequence == current.sequence:
        if previous != current:
            raise StateError("Usage snapshot sequence has conflicting facts.", code="usage_state_conflict")
        return previous.model_copy(deep=True)
    earlier, later = (previous, current) if previous.sequence < current.sequence else (current, previous)
    records = {record.record_id: record for record in later.records}
    if later.tool_calls < earlier.tool_calls:
        raise StateError("Usage snapshot lost observed tool calls.", code="usage_state_conflict")
    for record in earlier.records:
        retained = records.get(record.record_id)
        if retained is None:
            raise StateError("Usage snapshot lost an observed contribution.", code="usage_state_conflict")
        validate_contribution(record, retained)
    return later.model_copy(deep=True)


@runtime_checkable
class UsageReporter(Protocol):
    async def report(self, snapshot: UsageSnapshot) -> None:
        """Persist atomically and idempotently; own storage deadlines and safe cancellation.

        Normal delivery has no Harness deadline. Cancellation may redeliver an
        unacknowledged snapshot during bounded cleanup, never replay the Model.
        """
        ...


class UsageDelta(BaseModel):
    """Latest changed contributions since an acknowledged scope sequence, not additive charges."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: UsageScope
    after_sequence: int = Field(ge=0)
    records: tuple[Annotated[UsageRecord, Field(discriminator="kind")], ...] = Field(
        default=(), max_length=_MAX_RECORDS
    )

    @model_validator(mode="after")
    def _validate_delta(self) -> UsageDelta:
        if self.after_sequence >= self.scope.sequence:
            raise ValueError("Usage delta must advance its acknowledged sequence")
        _validate_records(self.scope, self.records)
        if len(dump_json_bytes(self.model_dump(mode="json"))) > 16 * 1024 * 1024:
            raise ValueError("Usage delta exceeds its 16 MiB bound")
        return self


@runtime_checkable
class UsageDeltaReporter(Protocol):
    async def report_delta(self, delta: UsageDelta) -> None:
        """Atomically persist changes and progress, acknowledging only on successful return.

        Unacknowledged changes remain pending for retry, independently of display delivery.
        A later delivery may cover an overlapping interval after an uncertain commit.
        Hosts must reject gaps, deduplicate retries and keep each scope's writer serialized.
        """
        ...


class UsageReportError(RunError):
    def __init__(self, message: str = "Host usage delivery failed.") -> None:
        super().__init__(message, code="usage_report_failed")


class RunUsageLedger:
    """Latest run-local facts plus pending delivery; no pre-call durable registration."""

    def __init__(
        self,
        *,
        run_id: str,
        instance: AgentInstanceContext,
        events: HarnessEventEmitter | None = None,
        reporter: UsageReporter | UsageDeltaReporter | None = None,
        limits: UsageLimits | None = None,
        baseline: RunUsage | None = None,
        thread_id: str | None = None,
        state: AgentContextState | None = None,
        snapshot: UsageSnapshot | None = None,
    ) -> None:
        self.run_id = snapshot.run_id if snapshot is not None else run_id
        self.usage_id = snapshot.usage_id if snapshot is not None else _stable_id("scope", run_id)
        self.thread_id = thread_id
        self.instance = (
            replace(
                instance,
                agent_instance_id=snapshot.agent_instance_id,
                parent_agent_instance_id=snapshot.parent_agent_instance_id,
                delegation_id=snapshot.delegation_id,
            )
            if snapshot is not None
            else instance
        )
        self._state = state
        self._sequence = snapshot.sequence if snapshot is not None else 0
        self._tool_calls = snapshot.tool_calls if snapshot is not None else 0
        self._events = events
        self.reporter = reporter
        self.cost_capability: AbstractModelCostCapability | None = None
        self._records = {r.record_id: r.model_copy(deep=True) for r in snapshot.records} if snapshot else {}
        # One latest value per changed record, retained until display acknowledges it.
        # Its process-local change sequence also selects unacknowledged persistence.
        self._pending: dict[str, tuple[int, UsageRecord]] = {}
        self._reported_sequence = self._sequence
        self._persisted_sequence = self._sequence
        self._model_ordinal = 1 + max(
            (r.response_ordinal for r in self._records.values() if isinstance(r, ModelUsageRecord)), default=-1
        )
        self._flush_lock = asyncio.Lock()
        self._limits = limits
        self._baseline = deepcopy(baseline) if baseline is not None else RunUsage()
        self.requests = RequestBudget(
            used=self._baseline.requests + sum(isinstance(r, ModelUsageRecord) for r in self._records.values()),
            limit=limits.request_limit if limits else None,
        )

    @property
    def records(self) -> tuple[UsageRecord, ...]:
        return tuple(record.model_copy(deep=True) for record in self._records.values())

    @property
    def snapshot(self) -> UsageSnapshot:
        return UsageSnapshot(
            usage_id=self.usage_id,
            thread_id=self.thread_id,
            run_id=self.run_id,
            agent_instance_id=self.instance.agent_instance_id,
            parent_agent_instance_id=self.instance.parent_agent_instance_id,
            delegation_id=self.instance.delegation_id,
            sequence=self._sequence,
            tool_calls=self._tool_calls,
            records=self.records,
        )

    async def save(self) -> None:
        """Publish pure state even when Host delivery failed or no event can be emitted."""
        async with self._flush_lock:
            if self._state is not None:
                await self._state.write(USAGE_CAPABILITY_ID, self.snapshot, version="1")

    def summary(self, *, tool_calls: int | None = None) -> RunUsageSummary:
        if tool_calls is not None:
            observed = max(0, tool_calls - self._baseline.tool_calls)
            if observed > self._tool_calls:
                self._tool_calls = observed
                self._sequence += 1
        return summarize_usage(self._records.values(), tool_calls=self._tool_calls)

    def generation(self, record_id: str) -> ModelUsageRecord:
        record = self._records.get(record_id)
        if not isinstance(record, ModelUsageRecord):
            raise StateError("Provider continuation has no matching usage contribution.", code="usage_state_missing")
        return record.model_copy(deep=True)

    def next_model_ordinal(self) -> int:
        ordinal = self._model_ordinal
        self._model_ordinal += 1
        return ordinal

    def _budget(self) -> RunUsage:
        summary = self.summary()
        value = RunUsage(
            **{name: getattr(summary, name) for name in TOKEN_COUNTERS},
            requests=summary.requests,
            cost=summary.cost,
        )
        value.incr(self._baseline)
        return value

    def reserve(self, call_id: str, *, continuation: bool = False) -> None:
        # No await between checking capacity and reserving a request.
        if not continuation and len(self._records) + self.requests.pending >= _MAX_RECORDS:
            raise RunError("Run usage capacity was exceeded.", code="usage_capacity_exceeded")
        if self._limits is not None:
            replace(self._limits, request_limit=None).check_before_request(self._budget())
        self.requests.limit = self._limits.request_limit if self._limits else None
        self.requests.reserve(call_id, continuation=continuation)

    def finish(
        self, call_id: str, record: ModelUsageRecord, host_budget: RequestBudget | None = None
    ) -> UsageLimitExceeded | None:
        """Capture the fact and settle both scopes before any asynchronous delivery."""
        self._append(record)
        refusal = self.requests.finish(call_id, record)
        if host_budget is not None:
            host_refusal = host_budget.finish(call_id, record)
            refusal = refusal or host_refusal
        try:
            self.check_limits()
        except UsageLimitExceeded as error:
            refusal = refusal or error
        return refusal

    def check_limits(self) -> None:
        if self._limits is not None:
            value = self._budget()
            self._limits.check_tokens(value)
            self._limits.check_cost(value, warn_if_cost_unavailable=False)

    async def _record_provider(
        self, usage: ProviderUsage, *, source: str, tool_id: str | None = None, tool_call_id: str | None = None
    ) -> ProviderUsageRecord:
        receipt = ProviderUsage.model_validate(usage.model_dump())
        record_id = _stable_id("provider", receipt.provider, receipt.product, receipt.usage_id)
        previous = self._records.get(record_id)
        candidate = ProviderUsageRecord(
            record_id=record_id,
            run_id=self.run_id,
            ordinal=previous.ordinal if isinstance(previous, ProviderUsageRecord) else len(self._records),
            agent_instance_id=self.instance.agent_instance_id,
            parent_agent_instance_id=self.instance.parent_agent_instance_id,
            delegation_id=self.instance.delegation_id,
            source=source,
            tool_id=tool_id,
            tool_call_id=tool_call_id,
            usage=receipt,
        )
        if isinstance(previous, ProviderUsageRecord):
            if previous.usage != receipt:
                raise RunError("A provider receipt changed its facts.", code="usage_record_conflict")
            return previous.model_copy(deep=True)
        self._append(candidate)
        await self._flush(reason="provider", trigger_record_id=record_id)
        self.check_limits()
        return candidate.model_copy(deep=True)

    def _append(self, record: UsageRecord) -> None:
        previous = self._records.get(record.record_id)
        if previous is not None:
            validate_contribution(previous, record)
            if record == previous:
                return
        elif len(self._records) >= _MAX_RECORDS:
            raise RunError("Run usage capacity was exceeded.", code="usage_capacity_exceeded")
        self._records[record.record_id] = record.model_copy(deep=True)
        self._sequence += 1
        self._pending[record.record_id] = (self._sequence, self._records[record.record_id])

    async def _flush(self, *, reason: UsageReportReason, trigger_record_id: str | None = None) -> None:
        try:
            await self._deliver(reason=reason, trigger_record_id=trigger_record_id)
        except asyncio.CancelledError:
            await self._flush_cleanup()
            raise

    async def _flush_cleanup(
        self,
        *,
        reason: UsageReportReason = "terminal",
        trigger_record_id: str | None = None,
        display: bool = False,
    ) -> None:
        """Bound usage cleanup while preserving a primary failure or cancellation."""
        try:
            with anyio.move_on_after(_USAGE_CLEANUP_SECONDS, shield=True) as cleanup:
                await self._deliver(reason=reason, trigger_record_id=trigger_record_id, display=display)
            if cleanup.cancelled_caught:
                logger.warning("Usage cleanup timed out", extra={"usage_id": self.usage_id})
        except Exception:
            # The original failure remains authoritative; retained facts are
            # still available to the Host's terminal reconciliation and recovery.
            logger.warning("Usage cleanup failed", extra={"usage_id": self.usage_id})

    async def _deliver(self, *, reason: UsageReportReason, trigger_record_id: str | None, display: bool = True) -> None:
        from a13n_harness.events import UsageReportPayload, emit_harness_event

        async with self._flush_lock:
            snapshot = self.snapshot
            pending = tuple(self._pending.values())
            if self._state is not None:
                await self._state.write(USAGE_CAPABILITY_ID, snapshot, version="1")
            if snapshot.sequence == self._reported_sequence:
                return
            if snapshot.sequence != self._persisted_sequence:
                if self.reporter is not None:
                    try:
                        if isinstance(self.reporter, UsageDeltaReporter):
                            await self.reporter.report_delta(
                                UsageDelta(
                                    scope=snapshot.scope,
                                    after_sequence=self._persisted_sequence,
                                    records=tuple(
                                        record.model_copy(deep=True)
                                        for sequence, record in pending
                                        if sequence > self._persisted_sequence
                                    ),
                                )
                            )
                        else:
                            await self.reporter.report(snapshot)
                    except asyncio.CancelledError as exc:
                        task = asyncio.current_task()
                        if task is not None and task.cancelling():
                            raise
                        raise UsageReportError("Host usage reporter was cancelled.") from exc
                    except Exception as exc:
                        raise UsageReportError() from exc
                self._persisted_sequence = snapshot.sequence
            if not display:
                return
            if self._events is not None:
                chunks = _report_chunks([record for _, record in pending])
                report_id = _stable_id("report", self.usage_id, str(snapshot.sequence))
                for index, chunk in enumerate(chunks):
                    await emit_harness_event(
                        self._events,
                        kind="usage",
                        payload=UsageReportPayload(
                            report_id=report_id,
                            usage_id=self.usage_id,
                            usage_sequence=snapshot.sequence,
                            reason=reason,
                            trigger_record_id=trigger_record_id,
                            chunk_index=index,
                            chunk_count=len(chunks),
                            records=tuple(record.model_dump(mode="json") for record in chunk),
                        ),
                    )
            for sequence, record in pending:
                if self._pending.get(record.record_id) == (sequence, record):
                    del self._pending[record.record_id]
            self._reported_sequence = snapshot.sequence


@dataclass(init=False)
class UsageCapability(AbstractCapability[AgentContext]):
    """Install one run-local meter around the public Model boundary."""

    id = USAGE_CAPABILITY_ID

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(USAGE_CAPABILITY_ID)
        if existing is not None:
            return existing
        replacement = _RunUsageCapability(ctx.deps)
        ctx.deps._record_run_capability(USAGE_CAPABILITY_ID, replacement)
        return replacement

    def get_ordering(self) -> CapabilityOrdering:
        from a13n_harness.models.capability import SelfHealingModelCapability

        return CapabilityOrdering(position="innermost", wraps=[SelfHealingModelCapability])


class _RunUsageCapability(UsageCapability):
    def __init__(self, context: AgentContext) -> None:
        self.context = context

    async def wrap_node_run(self, ctx, *, node, handler):
        try:
            return await handler(node)
        finally:
            self.context.usage_attribution.summary(tool_calls=ctx.usage.tool_calls)

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if ctx.deps is not self.context:
            raise DefinitionError("Usage owner cannot cross runs.", code="capability_scope_invalid")
        return self

    async def wrap_model_request(
        self, ctx: RunContext[AgentContext], *, request_context: ModelRequestContext, handler: WrapModelRequestHandler
    ) -> ModelResponse:
        from a13n_harness.metering import ModelUsageBinding, meter_request

        if ctx.deps is not self.context:
            raise DefinitionError("Usage owner cannot cross runs.", code="capability_scope_invalid")
        cost = ctx.deps._inherited_model_cost or ctx.capabilities.get(MODEL_COST_CAPABILITY_ID)
        if not isinstance(cost, AbstractModelCostCapability):
            raise DefinitionError("Missing model-cost capability.", code="capability_type_mismatch")
        ctx.deps.usage_attribution.cost_capability = cost
        return await meter_request(
            ModelUsageBinding(ctx.deps.usage_attribution, cost, owner=ctx.deps), ctx, request_context, handler
        )


def _stable_id(kind: str, *values: str) -> str:
    digest = hashlib.sha256("\x00".join((kind, *values)).encode("utf-8")).hexdigest()[:24]
    return f"usage-{digest}"


def _report_chunks(records: list[UsageRecord]) -> list[list[UsageRecord]]:
    chunks: list[list[UsageRecord]] = []
    current: list[UsageRecord] = []
    for record in records:
        candidate = [*current, record]
        encoded = dump_json_bytes([item.model_dump(mode="json") for item in candidate])
        if current and (len(candidate) > _MAX_REPORT_RECORDS or len(encoded) > _MAX_REPORT_BYTES):
            chunks.append(current)
            current = [record]
        else:
            current = candidate
        if len(dump_json_bytes([item.model_dump(mode="json") for item in current])) > _MAX_REPORT_BYTES:
            raise RunError("One usage record exceeds the report size bound.", code="usage_record_too_large")
    if current:
        chunks.append(current)
    return chunks


__all__ = [
    "TOKEN_COUNTERS",
    "BoundedRequestUsage",
    "ModelUsageObservation",
    "ModelUsageRecord",
    "ProviderUsage",
    "ProviderUsageRecord",
    "RunUsageLedger",
    "RunUsageSummary",
    "UsageAccumulator",
    "UsageCounters",
    "UsageDelta",
    "UsageDeltaReporter",
    "UsageMeasure",
    "UsageRecord",
    "UsageReportError",
    "UsageReporter",
    "UsageScope",
    "UsageSnapshot",
    "intersect_usage_limits",
    "select_usage_snapshot",
    "summarize_usage",
    "update_observation",
    "validate_contribution",
]
