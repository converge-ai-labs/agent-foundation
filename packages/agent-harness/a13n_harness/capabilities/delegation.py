"""Inline delegation composition, state, and fresh child binding authority."""

from __future__ import annotations

import re
from collections.abc import Awaitable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.usage import UsageLimits

from a13n_harness._json import dump_json_bytes
from a13n_harness.context import AgentContext, BuiltSubagent, RunBindings
from a13n_harness.errors import DefinitionError, StateError
from a13n_harness.input import RunInputValue
from a13n_harness.state import HarnessState

DELEGATION_CAPABILITY_ID = "a13n.delegation"
DELEGATION_RUN_CAPABILITY_ID = "a13n.delegation.run"
_DELEGATION_STATE_VERSION = "1"
_CHILD_ID_PATTERN = re.compile(r"^(?P<name>[a-z][a-z0-9_-]{0,62})-(?P<suffix>[0-9a-f]{4})$")


class InlineDelegationBinder(Protocol):
    """Fresh Host authority factory already bound to the active parent run."""

    def __call__(
        self,
        child: BuiltSubagent,
        input: RunInputValue,
        child_instance_id: str,
        continuation: bool,
        usage_limits: UsageLimits | None,
    ) -> Awaitable[RunBindings]: ...


@dataclass(kw_only=True)
class DelegationRunCapability(AbstractCapability[AgentContext]):
    """Fresh run attachment that authorizes and binds inline child runs."""

    id: str | None = DELEGATION_RUN_CAPABILITY_ID
    binder: InlineDelegationBinder = field()
    usage_limits: UsageLimits | None = None

    def __post_init__(self) -> None:
        if self.id != DELEGATION_RUN_CAPABILITY_ID:
            raise ValueError(f"DelegationRunCapability.id must be {DELEGATION_RUN_CAPABILITY_ID!r}")
        if not callable(self.binder):
            raise TypeError("DelegationRunCapability.binder must be callable")
        self.usage_limits = deepcopy(self.usage_limits)

    async def bind_inline(
        self,
        child: BuiltSubagent,
        input: RunInputValue,
        child_instance_id: str,
        continuation: bool,
        usage_limits: UsageLimits | None,
    ) -> RunBindings:
        """Return complete fresh child bindings for one authorized invocation."""
        bindings = await self.binder(
            child,
            input,
            child_instance_id,
            continuation,
            deepcopy(usage_limits),
        )
        if not isinstance(bindings, RunBindings):
            raise DefinitionError(
                "Inline delegation binder returned an invalid value.",
                code="delegation_binding_invalid",
            )
        return bindings


class DelegationConfiguration(BaseModel):
    """Definition-owned finite inline state and context budgets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_children: int = Field(default=256, gt=0, le=4096)
    max_state_bytes: int = Field(default=16 * 1024 * 1024, ge=64 * 1024, le=128 * 1024 * 1024)
    max_input_bytes: int = Field(default=128 * 1024, ge=1024, le=1024 * 1024)
    max_output_bytes: int = Field(default=256 * 1024, ge=1024, le=4 * 1024 * 1024)
    selected_history_messages: int = Field(default=16, ge=1, le=128)


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


class DelegationState(BaseModel):
    """Capability-owned map of stable child references to nested Harness State."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    children: dict[str, InlineSubagentState] = Field(default_factory=dict)

    @field_validator("children")
    @classmethod
    def _validate_keys(cls, value: dict[str, InlineSubagentState]) -> dict[str, InlineSubagentState]:
        if any(key != child.child_instance_id for key, child in value.items()):
            raise ValueError("delegation state keys must match child_instance_id")
        return value


@dataclass(init=False)
class DelegationCapability(AbstractCapability[AgentContext]):
    """Compose the standard unified blocking delegation Toolset for one Agent."""

    id = DELEGATION_CAPABILITY_ID

    def __init__(self, configuration: DelegationConfiguration | None = None) -> None:
        self.configuration = (configuration or DelegationConfiguration()).model_copy(deep=True)

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(DELEGATION_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, _DelegationActiveCapability):
                raise DefinitionError(
                    "Delegation has an incompatible run replacement.",
                    code="capability_type_mismatch",
                )
            return existing
        if DELEGATION_CAPABILITY_ID not in ctx.deps._capability_provenance.definition_ids:
            raise DefinitionError(
                "DelegationCapability must originate from the Agent definition.",
                code="capability_scope_invalid",
            )
        state = (
            await ctx.deps.state.read(
                DELEGATION_CAPABILITY_ID,
                DelegationState,
                version=_DELEGATION_STATE_VERSION,
            )
            or DelegationState()
        )
        _validate_delegation_state(state, ctx.deps, self.configuration)
        replacement = _DelegationActiveCapability(
            self.configuration,
            context=ctx.deps,
            state=state,
        )
        ctx.deps._record_run_capability(DELEGATION_CAPABILITY_ID, replacement)
        return replacement


@dataclass(init=False)
class _DelegationActiveCapability(DelegationCapability):
    def __init__(
        self,
        configuration: DelegationConfiguration,
        *,
        context: AgentContext,
        state: DelegationState,
    ) -> None:
        super().__init__(configuration)
        from a13n_harness.toolsets.delegation import DelegationToolset

        self._context = context
        self._toolset = DelegationToolset(
            owner=self,
            context=context,
            configuration=self.configuration,
            state=state,
        )

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if ctx.deps is not self._context:
            raise DefinitionError(
                "Delegation run replacement cannot cross logical runs.",
                code="capability_scope_invalid",
            )
        return self

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return self._toolset.get_toolset()


def _validate_delegation_state(
    state: DelegationState,
    context: AgentContext,
    configuration: DelegationConfiguration,
) -> None:
    _validate_delegation_thread_identities(state, parent_thread_id=context.thread_id)
    if len(state.children) > configuration.max_children:
        raise StateError(
            "Delegation State contains too many inline children.",
            code="delegation_state_limit_exceeded",
        )
    for child_id, record in state.children.items():
        try:
            child = context.subagents.require(record.subagent_name)
        except KeyError as exc:
            raise StateError(
                "Delegation State references an unavailable child definition.",
                code="delegation_state_incompatible",
                details={"child_instance_id": child_id},
            ) from exc
        if child.definition.definition_id != record.child_definition_id:
            raise StateError(
                "Delegation State child definition is incompatible with the current Agent.",
                code="delegation_state_incompatible",
                details={"child_instance_id": child_id},
            )
    encoded = dump_json_bytes(state.model_dump(mode="json"), sort_keys=True)
    if len(encoded) > configuration.max_state_bytes:
        raise StateError(
            "Delegation State exceeds its encoded size limit.",
            code="delegation_state_limit_exceeded",
        )


def _validate_delegation_thread_identities(
    state: DelegationState,
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
                    "Delegation State reuses a Thread identity.",
                    code="delegation_state_incompatible",
                    details={"child_instance_id": child_id},
                )
            seen.add(thread_id)
            entry = record.state.agent_context_state.entries.get(DELEGATION_CAPABILITY_ID)
            if entry is None:
                continue
            if entry.version != _DELEGATION_STATE_VERSION:
                raise StateError(
                    "Nested Delegation State has an unsupported version.",
                    code="delegation_state_incompatible",
                    details={"child_instance_id": child_id},
                )
            try:
                pending.append(DelegationState.model_validate(entry.data))
            except ValueError as exc:
                raise StateError(
                    "Nested Delegation State is invalid.",
                    code="delegation_state_incompatible",
                    details={"child_instance_id": child_id},
                ) from exc


__all__ = [
    "DELEGATION_CAPABILITY_ID",
    "DELEGATION_RUN_CAPABILITY_ID",
    "DelegationCapability",
    "DelegationConfiguration",
    "DelegationRunCapability",
    "DelegationState",
    "InlineDelegationBinder",
    "InlineSubagentState",
]
