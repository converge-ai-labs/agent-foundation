"""Fresh run-scoped policy collaborators for managed tool invocation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError, field_validator
from pydantic_ai.capabilities import AbstractCapability

from a13n_harness._json import dump_json_bytes
from a13n_harness.context import AgentContext
from a13n_harness.identity import AgentInstanceContext
from a13n_harness.tools.metadata import CanonicalResource, HarnessToolMetadata

INVOCATION_POLICY_CAPABILITY_ID = "a13n.invocation-policy"

type InvocationDecisionKind = Literal["allow", "deny", "approval_required"]

_APPROVAL_METADATA_ADAPTER = TypeAdapter(dict[str, JsonValue])
_MAX_APPROVAL_METADATA_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class ToolInvocationContext:
    """Trusted, bounded identity and arguments for one provider dispatch."""

    invocation_id: str
    tool_call_id: str
    run_id: str
    instance: AgentInstanceContext
    tool_id: str
    toolset_id: str | None
    tool_name: str
    normalized_arguments: Mapping[str, JsonValue]
    arguments_digest: str
    resources: tuple[CanonicalResource, ...]
    idempotency_key: str | None
    deadline: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_arguments", MappingProxyType(dict(self.normalized_arguments)))
        object.__setattr__(self, "resources", tuple(resource.model_copy(deep=True) for resource in self.resources))


class InvocationGrantRef(BaseModel):
    """Opaque reference to a Host-issued provider invocation grant."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    grant_id: str = Field(min_length=1, max_length=256)
    audience: str = Field(min_length=1, max_length=256)
    claims_digest: str = Field(min_length=1, max_length=256)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        return value


@dataclass(frozen=True, slots=True)
class InvocationPolicyDecision:
    """One bounded live policy decision."""

    decision: InvocationDecisionKind
    reason: str | None = None
    approval_metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.decision not in {"allow", "deny", "approval_required"}:
            raise ValueError("Unsupported invocation policy decision")
        if self.reason is not None and (not self.reason.strip() or len(self.reason) > 512):
            raise ValueError("Policy reason must be a bounded non-blank string")
        try:
            approval_metadata = _APPROVAL_METADATA_ADAPTER.validate_python(
                dict(self.approval_metadata),
                strict=True,
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise ValueError("Approval metadata must be JSON-safe") from exc
        try:
            encoded_metadata = dump_json_bytes(approval_metadata, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("Approval metadata must contain finite JSON values") from exc
        if len(encoded_metadata) > _MAX_APPROVAL_METADATA_BYTES:
            raise ValueError("Approval metadata is too large")
        object.__setattr__(self, "approval_metadata", MappingProxyType(approval_metadata))

    @classmethod
    def allow(cls) -> InvocationPolicyDecision:
        return cls("allow")

    @classmethod
    def deny(cls, reason: str | None = None) -> InvocationPolicyDecision:
        return cls("deny", reason=reason)

    @classmethod
    def require_approval(
        cls,
        reason: str | None = None,
        *,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> InvocationPolicyDecision:
        return cls("approval_required", reason=reason, approval_metadata=metadata or {})


@runtime_checkable
class InvocationPolicyEvaluator(Protocol):
    """Authorize one fully prepared managed invocation."""

    async def __call__(
        self,
        invocation: ToolInvocationContext,
        metadata: HarnessToolMetadata,
        *,
        context: AgentContext,
    ) -> InvocationPolicyDecision: ...


@dataclass(slots=True)
class CredentialLease:
    """Opaque audience-bound value exposed only through the invocation task scope."""

    audience: str
    value: object = field(repr=False)
    close_callback: Callable[[], Awaitable[None]] | None = field(default=None, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.close_callback is not None:
            await self.close_callback()


@runtime_checkable
class CredentialBroker(Protocol):
    """Acquire one short-lived credential lease after authorization."""

    async def acquire(
        self,
        audience: str,
        invocation: ToolInvocationContext,
        *,
        context: AgentContext,
    ) -> CredentialLease: ...


@runtime_checkable
class InvocationGrantBroker(Protocol):
    """Issue an optional provider-verifiable invocation grant reference."""

    async def issue(
        self,
        invocation: ToolInvocationContext,
        metadata: HarnessToolMetadata,
        *,
        context: AgentContext,
    ) -> InvocationGrantRef | None: ...


@dataclass(kw_only=True)
class InvocationPolicyCapability(AbstractCapability[AgentContext]):
    """Fresh typed authority attachment selected from RunBindings.capabilities."""

    id: str | None = INVOCATION_POLICY_CAPABILITY_ID
    evaluator: InvocationPolicyEvaluator
    strict_managed_tools: bool = False
    credential_broker: CredentialBroker | None = None
    grant_broker: InvocationGrantBroker | None = None
    max_dispatch_retries: int = 1

    def __post_init__(self) -> None:
        if self.id != INVOCATION_POLICY_CAPABILITY_ID:
            raise ValueError(f"InvocationPolicyCapability.id must be {INVOCATION_POLICY_CAPABILITY_ID!r}")
        if not isinstance(self.evaluator, InvocationPolicyEvaluator):
            raise TypeError("evaluator must implement InvocationPolicyEvaluator")
        if not isinstance(self.strict_managed_tools, bool):
            raise TypeError("strict_managed_tools must be a boolean")
        if self.credential_broker is not None and not isinstance(self.credential_broker, CredentialBroker):
            raise TypeError("credential_broker must implement CredentialBroker")
        if self.grant_broker is not None and not isinstance(self.grant_broker, InvocationGrantBroker):
            raise TypeError("grant_broker must implement InvocationGrantBroker")
        if not isinstance(self.max_dispatch_retries, int) or not 0 <= self.max_dispatch_retries <= 3:
            raise ValueError("max_dispatch_retries must be between zero and three")


@dataclass(frozen=True, slots=True)
class InvocationScope:
    """Task-local first-party adapter inputs, never serialized or emitted."""

    invocation: ToolInvocationContext
    credentials: Mapping[str, object]
    grant: InvocationGrantRef | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "credentials", MappingProxyType(dict(self.credentials)))
