"""Harness-owned inline delegation and Host-owned asynchronous subagent boundary."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.usage import UsageLimits

from a13n_harness.context import AgentContext, BuiltSubagent
from a13n_harness.errors import DefinitionError, StateError
from a13n_harness.identity import AgentIdentityRef
from a13n_harness.input import RunInputValue
from a13n_harness.state import HarnessState

if TYPE_CHECKING:
    from a13n_harness.execution import DelegationContextPolicy

SUBAGENT_CAPABILITY_ID = "a13n.subagents"
MAX_SUBAGENT_ACTIVITY_OUTPUT_CHARS = 32 * 1024
_INLINE_SUBAGENT_STATE_VERSION = "1"
_CHILD_ID_PATTERN = re.compile(r"^(?P<name>[a-z][a-z0-9_-]{0,62})-(?P<suffix>[0-9a-f]{4})$")
_DELEGATION_INTENT_ID_PATTERN = re.compile(r"^sdi_[0-9a-f]{64}$")
_MAX_PROMPT_LENGTH = 1024 * 1024
_MAX_EXECUTION_PAGE = 100

type SubagentStatus = Literal["running", "succeeded", "failed", "cancelled", "lost"]
type SubagentToolCallStatus = Literal["running", "success", "failed", "denied", "interrupted"]


class InlineSubagentState(BaseModel):
    """Latest complete continuation for one compact inline child reference."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    child_instance_id: str = Field(min_length=7, max_length=68)
    subagent_name: str = Field(min_length=1, max_length=63)
    child_definition_id: str = Field(min_length=1, max_length=256)
    state: HarnessState

    @model_validator(mode="after")
    def _validate_identity(self) -> InlineSubagentState:
        match = _CHILD_ID_PATTERN.fullmatch(self.child_instance_id)
        if match is None or match.group("name") != self.subagent_name:
            raise ValueError("inline child identity is not canonical")
        return self


class InlineSubagentCollectionState(BaseModel):
    """Capability-owned inline child continuations stored in parent Agent state."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    children: dict[str, InlineSubagentState] = Field(default_factory=dict)

    @field_validator("children")
    @classmethod
    def _validate_keys(cls, value: dict[str, InlineSubagentState]) -> dict[str, InlineSubagentState]:
        if any(key != child.child_instance_id for key, child in value.items()):
            raise ValueError("inline subagent state keys must match child_instance_id")
        return value


class SubagentToolCallSnapshot(BaseModel):
    """Bounded Host projection of one current or recent child Tool call."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    tool_call_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=256)
    status: SubagentToolCallStatus
    arguments: JsonValue | None = None
    result: JsonValue | None = None


class SubagentActivitySnapshot(BaseModel):
    """Bounded Host projection of recent child output and Tool activity."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    sequence: int = Field(ge=0)
    output_preview: str = Field(default="", max_length=MAX_SUBAGENT_ACTIVITY_OUTPUT_CHARS)
    output_truncated: bool = False
    active_tool_calls: tuple[SubagentToolCallSnapshot, ...] = Field(default=(), max_length=20)
    recent_tool_calls: tuple[SubagentToolCallSnapshot, ...] = Field(default=(), max_length=20)
    dropped_tool_calls: int = Field(default=0, ge=0)


class AsyncDelegateRequest(BaseModel):
    """Validated async child admission request passed to the Host operator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subagent_name: str = Field(min_length=1, max_length=63)
    prompt: str = Field(min_length=1, max_length=_MAX_PROMPT_LENGTH)
    delegation_intent_id: str = Field(min_length=68, max_length=68)

    @field_validator("delegation_intent_id")
    @classmethod
    def _validate_delegation_intent_id(cls, value: str) -> str:
        if _DELEGATION_INTENT_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("delegation intent identity is not canonical")
        return value


class AsyncResumeRequest(BaseModel):
    """Validated linked-continuation request passed to the Host operator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(min_length=1, max_length=256)
    prompt: str = Field(min_length=1, max_length=_MAX_PROMPT_LENGTH)


class SubagentInfoRequest(BaseModel):
    """Validated async execution inspection request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str | None = Field(default=None, min_length=1, max_length=256)
    execution_offset: int = Field(default=0, ge=0)
    execution_limit: int = Field(default=20, ge=1, le=_MAX_EXECUTION_PAGE)


class SubagentWaitRequest(BaseModel):
    """Validated bounded wait or fan-in request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str | None = Field(default=None, min_length=1, max_length=256)
    timeout_seconds: float | None = Field(default=None, gt=0)
    execution_offset: int = Field(default=0, ge=0)
    execution_limit: int = Field(default=20, ge=1, le=_MAX_EXECUTION_PAGE)


class SubagentSteerRequest(BaseModel):
    """Validated steering request for one active Host execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(min_length=1, max_length=256)
    message: str = Field(min_length=1, max_length=_MAX_PROMPT_LENGTH)


class SubagentCancelRequest(BaseModel):
    """Validated cancellation request for one Host execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(min_length=1, max_length=256)


class AsyncExecutionView(BaseModel):
    """Compact accepted execution projection returned by delegate or resume."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    execution_id: str = Field(min_length=1, max_length=256)
    subagent_name: str = Field(min_length=1, max_length=63)
    child_definition_id: str = Field(min_length=1, max_length=256)
    status: SubagentStatus
    resumed_from: str | None = Field(default=None, min_length=1, max_length=256)
    failure: JsonValue | None = None
    resumable: bool = False
    thread_id: str | None = Field(default=None, min_length=1, max_length=256)
    child_run_id: str | None = Field(default=None, min_length=1, max_length=256)
    segment_index: int | None = Field(default=None, ge=0)


class SubagentExecutionView(AsyncExecutionView):
    """Bounded current execution view returned by info or wait."""

    input: str | None = Field(default=None, max_length=_MAX_PROMPT_LENGTH)
    activity: SubagentActivitySnapshot | None = None


class SubagentInfoResult(BaseModel):
    """One bounded page of current Host execution projections."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    executions: tuple[SubagentExecutionView, ...] = Field(default=(), max_length=_MAX_EXECUTION_PAGE)
    execution_offset: int = Field(default=0, ge=0)
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class SubagentWaitResult(BaseModel):
    """One bounded wait result or fan-in page from the Host operator."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    executions: tuple[SubagentExecutionView, ...] = Field(default=(), max_length=_MAX_EXECUTION_PAGE)
    execution_offset: int = Field(default=0, ge=0)
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class SubagentSteerResult(BaseModel):
    """Host acknowledgement for one steering request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(min_length=1, max_length=256)
    accepted: bool
    enqueue_id: str | None = Field(default=None, min_length=1, max_length=256)


class SubagentCancelResult(BaseModel):
    """Host acknowledgement for one cancellation request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(min_length=1, max_length=256)
    accepted: bool
    status: SubagentStatus | None = None


@dataclass(frozen=True, slots=True)
class SubagentOperatorContext:
    """Detached parent correlation supplied to each Host operator use case."""

    parent_thread_id: str
    parent_run_id: str
    parent_agent_instance_id: str
    host_refs: Mapping[str, str]

    def __post_init__(self) -> None:
        values = (self.parent_thread_id, self.parent_run_id, self.parent_agent_instance_id)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("subagent operator parent correlation must be non-blank")
        object.__setattr__(self, "host_refs", MappingProxyType(dict(self.host_refs)))


@dataclass(frozen=True, slots=True)
class ResolvedDelegationContext:
    """Detached child input produced after applying one authored context policy."""

    input: RunInputValue
    policy: DelegationContextPolicy

    def __post_init__(self) -> None:
        object.__setattr__(self, "input", deepcopy(self.input))
        object.__setattr__(self, "policy", deepcopy(self.policy))


@dataclass(frozen=True, slots=True)
class SubagentDelegationPlan:
    """Immutable Harness-resolved child authority ceiling for one Host admission."""

    child: BuiltSubagent
    child_identity: AgentIdentityRef
    context: ResolvedDelegationContext
    usage_limits: UsageLimits | None
    parent: SubagentOperatorContext

    def __post_init__(self) -> None:
        if not isinstance(self.child, BuiltSubagent):
            raise TypeError("child must be a BuiltSubagent")
        if not isinstance(self.child_identity, AgentIdentityRef):
            raise TypeError("child_identity must be an AgentIdentityRef")
        if not isinstance(self.context, ResolvedDelegationContext):
            raise TypeError("context must be a ResolvedDelegationContext")
        if self.usage_limits is not None and not isinstance(self.usage_limits, UsageLimits):
            raise TypeError("usage_limits must be UsageLimits or None")
        if not isinstance(self.parent, SubagentOperatorContext):
            raise TypeError("parent must be a SubagentOperatorContext")
        object.__setattr__(self, "usage_limits", deepcopy(self.usage_limits))


class SubagentOperator(ABC):
    """Host-owned complete asynchronous subagent use-case boundary."""

    @abstractmethod
    async def delegate(
        self,
        plan: SubagentDelegationPlan,
        request: AsyncDelegateRequest,
    ) -> AsyncExecutionView: ...

    @abstractmethod
    async def info(
        self,
        context: SubagentOperatorContext,
        request: SubagentInfoRequest,
    ) -> SubagentInfoResult: ...

    @abstractmethod
    async def wait(
        self,
        context: SubagentOperatorContext,
        request: SubagentWaitRequest,
    ) -> SubagentWaitResult: ...

    @abstractmethod
    async def steer(
        self,
        context: SubagentOperatorContext,
        request: SubagentSteerRequest,
    ) -> SubagentSteerResult: ...

    @abstractmethod
    async def cancel(
        self,
        context: SubagentOperatorContext,
        request: SubagentCancelRequest,
    ) -> SubagentCancelResult: ...

    @abstractmethod
    async def resume(
        self,
        plan: SubagentDelegationPlan,
        request: AsyncResumeRequest,
    ) -> AsyncExecutionView: ...


@dataclass(init=False)
class SubagentCapability(AbstractCapability[AgentContext]):
    """Select the standard inline surface or an explicit Host async operator."""

    id = SUBAGENT_CAPABILITY_ID

    def __init__(
        self,
        *,
        async_enabled: bool = False,
        operator: SubagentOperator | None = None,
    ) -> None:
        if not isinstance(async_enabled, bool):
            raise TypeError("async_enabled must be a boolean")
        if async_enabled and not isinstance(operator, SubagentOperator):
            raise DefinitionError(
                "Async subagents require a Host SubagentOperator.",
                code="subagent_operator_required",
            )
        if not async_enabled and operator is not None:
            raise DefinitionError(
                "Inline subagents do not accept a Host SubagentOperator.",
                code="subagent_operator_unexpected",
            )
        self._async_enabled = async_enabled
        self._operator = operator

    @property
    def async_enabled(self) -> bool:
        return self._async_enabled

    @property
    def operator(self) -> SubagentOperator | None:
        return self._operator

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(SUBAGENT_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, _SubagentActiveCapability):
                raise DefinitionError(
                    "Subagent Capability has an incompatible logical-run replacement.",
                    code="capability_type_mismatch",
                )
            return existing
        if SUBAGENT_CAPABILITY_ID not in ctx.deps._capability_provenance.definition_ids:
            raise DefinitionError(
                "SubagentCapability must originate from the Agent definition.",
                code="capability_scope_invalid",
            )

        if self.async_enabled:
            assert self.operator is not None
            replacement: _SubagentActiveCapability = _AsyncSubagentCapability(
                context=ctx.deps,
                operator=self.operator,
            )
        else:
            state = (
                await ctx.deps.state.read(
                    SUBAGENT_CAPABILITY_ID,
                    InlineSubagentCollectionState,
                    version=_INLINE_SUBAGENT_STATE_VERSION,
                )
                or InlineSubagentCollectionState()
            )
            _validate_inline_subagent_state(state, ctx.deps)
            replacement = _InlineSubagentCapability(context=ctx.deps, state=state)
        ctx.deps._record_run_capability(SUBAGENT_CAPABILITY_ID, replacement)
        return replacement


@dataclass(init=False)
class _SubagentActiveCapability(SubagentCapability):
    def __init__(
        self,
        *,
        context: AgentContext,
        async_enabled: bool,
        operator: SubagentOperator | None,
    ) -> None:
        super().__init__(async_enabled=async_enabled, operator=operator)
        self._context = context

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if ctx.deps is not self._context:
            raise DefinitionError(
                "Subagent run replacement cannot cross logical runs.",
                code="capability_scope_invalid",
            )
        return self


@dataclass(init=False)
class _InlineSubagentCapability(_SubagentActiveCapability):
    def __init__(self, *, context: AgentContext, state: InlineSubagentCollectionState) -> None:
        from a13n_harness.toolsets.delegation import DelegationToolset

        super().__init__(context=context, async_enabled=False, operator=None)
        self._toolset = DelegationToolset(owner=self, context=context, state=state)

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return self._toolset.get_toolset()


@dataclass(init=False)
class _AsyncSubagentCapability(_SubagentActiveCapability):
    def __init__(self, *, context: AgentContext, operator: SubagentOperator) -> None:
        from a13n_harness.toolsets.subagents import AsyncSubagentToolset

        super().__init__(context=context, async_enabled=True, operator=operator)
        self._toolset = AsyncSubagentToolset(
            owner=self,
            context=context,
            operator=operator,
        )

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return self._toolset.get_toolset()


def _validate_inline_subagent_state(state: InlineSubagentCollectionState, context: AgentContext) -> None:
    _validate_inline_thread_identities(state, parent_thread_id=context.thread_id)
    for child_id, record in state.children.items():
        try:
            child = context.subagents.require(record.subagent_name)
        except KeyError as exc:
            raise StateError(
                "Inline subagent State references an unavailable child definition.",
                code="subagent_state_incompatible",
                details={"child_instance_id": child_id},
            ) from exc
        if child.definition.definition_id != record.child_definition_id:
            raise StateError(
                "Inline subagent State child definition is incompatible with the current Agent.",
                code="subagent_state_incompatible",
                details={"child_instance_id": child_id},
            )


def _validate_inline_thread_identities(
    state: InlineSubagentCollectionState,
    *,
    parent_thread_id: str,
) -> None:
    seen = {parent_thread_id}
    pending = [state]
    while pending:
        current = pending.pop()
        for child_id, record in current.children.items():
            thread_id = record.state.thread_id
            if thread_id in seen:
                raise StateError(
                    "Inline subagent State reuses a Thread identity.",
                    code="subagent_state_incompatible",
                    details={"child_instance_id": child_id},
                )
            seen.add(thread_id)
            entry = record.state.agent_context_state.entries.get(SUBAGENT_CAPABILITY_ID)
            if entry is None:
                continue
            if entry.version != _INLINE_SUBAGENT_STATE_VERSION:
                raise StateError(
                    "Nested inline subagent State has an unsupported version.",
                    code="subagent_state_incompatible",
                    details={"child_instance_id": child_id},
                )
            try:
                pending.append(InlineSubagentCollectionState.model_validate(entry.data))
            except ValueError as exc:
                raise StateError(
                    "Nested inline subagent State is invalid.",
                    code="subagent_state_incompatible",
                    details={"child_instance_id": child_id},
                ) from exc


__all__ = [
    "MAX_SUBAGENT_ACTIVITY_OUTPUT_CHARS",
    "SUBAGENT_CAPABILITY_ID",
    "AsyncDelegateRequest",
    "AsyncExecutionView",
    "AsyncResumeRequest",
    "InlineSubagentCollectionState",
    "InlineSubagentState",
    "ResolvedDelegationContext",
    "SubagentActivitySnapshot",
    "SubagentCancelRequest",
    "SubagentCancelResult",
    "SubagentCapability",
    "SubagentDelegationPlan",
    "SubagentExecutionView",
    "SubagentInfoRequest",
    "SubagentInfoResult",
    "SubagentOperator",
    "SubagentOperatorContext",
    "SubagentStatus",
    "SubagentSteerRequest",
    "SubagentSteerResult",
    "SubagentToolCallSnapshot",
    "SubagentToolCallStatus",
    "SubagentWaitRequest",
    "SubagentWaitResult",
]
