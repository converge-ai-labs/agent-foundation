"""Optional metadata for Harness-managed Pydantic AI function tools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai.tools import Tool, ToolFuncEither

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError

HARNESS_TOOL_METADATA_KEY = "a13n.harness.tool"
MAX_TOOL_ID_LENGTH = 256
MAX_CREDENTIAL_AUDIENCES = 16
MAX_OUTPUT_BYTES = 512 * 1024 * 1024
MAX_INLINE_BYTES = 256 * 1024

type ToolEffect = Literal["read", "write", "delete", "execute", "external_communication"]
type IdempotencySemantics = Literal["none", "read_only", "provider_key"]
type OutputOverflow = Literal["fail", "truncate", "spill"]

_VALID_EFFECTS = frozenset({"read", "write", "delete", "execute", "external_communication"})
_VALID_IDEMPOTENCY = frozenset({"none", "read_only", "provider_key"})


class CanonicalResource(BaseModel):
    """One provider-resolved policy resource identity."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    namespace: str = Field(min_length=1, max_length=256)
    kind: str = Field(min_length=1, max_length=256)
    identifier: str = Field(min_length=1, max_length=2048)


class ToolOutputPolicy(BaseModel):
    """Finite model-visible output limits for one managed tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_inline_bytes: int = Field(ge=512, le=MAX_INLINE_BYTES)
    max_output_bytes: int = Field(gt=0, le=MAX_OUTPUT_BYTES)
    overflow: OutputOverflow = "spill"
    redact: bool = True

    @field_validator("max_output_bytes")
    @classmethod
    def _validate_total_limit(cls, value: int, info: Any) -> int:
        inline = info.data.get("max_inline_bytes")
        if isinstance(inline, int) and value < inline:
            raise ValueError("max_output_bytes must be greater than or equal to max_inline_bytes")
        return value


@runtime_checkable
class ToolResourceResolver(Protocol):
    """Resolve typed tool arguments into policy resource identities."""

    async def __call__(
        self,
        arguments: Mapping[str, object],
        *,
        context: AgentContext,
    ) -> tuple[CanonicalResource, ...]: ...


@dataclass(frozen=True, slots=True)
class HarnessToolMetadata:
    """Trusted semantics that opt one native function tool into managed dispatch."""

    tool_id: str
    effects: frozenset[ToolEffect]
    credential_audiences: tuple[str, ...]
    idempotency: IdempotencySemantics
    output_policy: ToolOutputPolicy
    resource_resolver: ToolResourceResolver | None = None
    superseded_by_tool_ids: frozenset[str] = frozenset()
    shell_review: bool = False

    def __post_init__(self) -> None:
        normalized = _normalize_metadata_fields(
            tool_id=self.tool_id,
            effects=self.effects,
            credential_audiences=self.credential_audiences,
            idempotency=self.idempotency,
            output_policy=self.output_policy,
            resource_resolver=self.resource_resolver,
            superseded_by_tool_ids=self.superseded_by_tool_ids,
            shell_review=self.shell_review,
        )
        object.__setattr__(self, "tool_id", normalized["tool_id"])
        object.__setattr__(self, "effects", normalized["effects"])
        object.__setattr__(self, "credential_audiences", normalized["credential_audiences"])
        object.__setattr__(self, "idempotency", normalized["idempotency"])
        object.__setattr__(self, "output_policy", normalized["output_policy"])
        object.__setattr__(self, "resource_resolver", normalized["resource_resolver"])
        object.__setattr__(self, "superseded_by_tool_ids", normalized["superseded_by_tool_ids"])


class HarnessTool(Tool[AgentContext]):
    """Thin native Tool helper that attaches complete managed metadata."""

    def __init__(
        self,
        function: ToolFuncEither[AgentContext, ...],
        *,
        harness_metadata: HarnessToolMetadata,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(harness_metadata, HarnessToolMetadata):
            raise DefinitionError("harness_metadata must be HarnessToolMetadata.", code="tool_metadata_invalid")
        combined = dict(metadata or {})
        if HARNESS_TOOL_METADATA_KEY in combined:
            raise DefinitionError(
                "The reserved Harness tool metadata key cannot be supplied twice.",
                code="tool_metadata_reserved",
            )
        combined[HARNESS_TOOL_METADATA_KEY] = harness_metadata
        super().__init__(function, metadata=combined, **kwargs)


def normalize_harness_tool_metadata(value: object) -> HarnessToolMetadata:
    """Validate and detach metadata read from a prepared ToolDefinition."""
    try:
        if isinstance(value, HarnessToolMetadata):
            return HarnessToolMetadata(
                tool_id=value.tool_id,
                effects=value.effects,
                credential_audiences=value.credential_audiences,
                idempotency=value.idempotency,
                output_policy=value.output_policy.model_copy(deep=True),
                resource_resolver=value.resource_resolver,
                superseded_by_tool_ids=value.superseded_by_tool_ids,
                shell_review=value.shell_review,
            )
        if not isinstance(value, Mapping):
            raise TypeError("metadata must be HarnessToolMetadata or a mapping")
        expected = {
            "tool_id",
            "effects",
            "credential_audiences",
            "idempotency",
            "output_policy",
            "resource_resolver",
            "superseded_by_tool_ids",
            "shell_review",
        }
        extra = set(value) - expected
        required = expected - {"resource_resolver", "superseded_by_tool_ids", "shell_review"}
        missing = required - set(value)
        if extra or missing:
            raise ValueError("metadata fields do not match the managed contract")
        fields = _normalize_metadata_fields(
            tool_id=value["tool_id"],
            effects=value["effects"],
            credential_audiences=value["credential_audiences"],
            idempotency=value["idempotency"],
            output_policy=value["output_policy"],
            resource_resolver=value.get("resource_resolver"),
            superseded_by_tool_ids=value.get("superseded_by_tool_ids", ()),
            shell_review=value.get("shell_review", False),
        )
        return HarnessToolMetadata(**fields)
    except DefinitionError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise DefinitionError("Harness tool metadata is invalid.", code="tool_metadata_invalid") from exc


def _normalize_metadata_fields(
    *,
    tool_id: object,
    effects: object,
    credential_audiences: object,
    idempotency: object,
    output_policy: object,
    resource_resolver: object,
    superseded_by_tool_ids: object,
    shell_review: object,
) -> dict[str, Any]:
    if not isinstance(tool_id, str) or not tool_id.strip() or len(tool_id.strip()) > MAX_TOOL_ID_LENGTH:
        raise DefinitionError("Managed tool_id is invalid.", code="tool_metadata_invalid")
    if isinstance(effects, (str, bytes)):
        raise DefinitionError("Managed effects must be a collection.", code="tool_metadata_invalid")
    try:
        normalized_effects = frozenset(effects)  # type: ignore[arg-type]
    except TypeError as exc:
        raise DefinitionError("Managed effects must be a collection.", code="tool_metadata_invalid") from exc
    if not normalized_effects or not normalized_effects <= _VALID_EFFECTS:
        raise DefinitionError("Managed effects are invalid.", code="tool_metadata_invalid")

    if isinstance(credential_audiences, (str, bytes)):
        raise DefinitionError("Credential audiences must be a collection.", code="tool_metadata_invalid")
    try:
        audiences = tuple(credential_audiences)  # type: ignore[arg-type]
    except TypeError as exc:
        raise DefinitionError("Credential audiences must be a collection.", code="tool_metadata_invalid") from exc
    if len(audiences) > MAX_CREDENTIAL_AUDIENCES or not all(
        isinstance(item, str) and item.strip() and len(item.strip()) <= 256 for item in audiences
    ):
        raise DefinitionError("Credential audiences are invalid.", code="tool_metadata_invalid")
    normalized_audiences = tuple(item.strip() for item in audiences)
    if len(set(normalized_audiences)) != len(normalized_audiences):
        raise DefinitionError("Credential audiences are invalid.", code="tool_metadata_invalid")

    if not isinstance(idempotency, str) or idempotency not in _VALID_IDEMPOTENCY:
        raise DefinitionError("Managed idempotency semantics are invalid.", code="tool_metadata_invalid")
    try:
        policy = (
            output_policy.model_copy(deep=True)
            if isinstance(output_policy, ToolOutputPolicy)
            else ToolOutputPolicy.model_validate(output_policy, strict=True)
        )
    except (TypeError, ValueError) as exc:
        raise DefinitionError("Managed output policy is invalid.", code="tool_metadata_invalid") from exc
    if resource_resolver is not None and not callable(resource_resolver):
        raise DefinitionError("Managed resource_resolver must be callable.", code="tool_metadata_invalid")
    if not isinstance(shell_review, bool):
        raise DefinitionError("Managed shell_review marker must be a boolean.", code="tool_metadata_invalid")

    if isinstance(superseded_by_tool_ids, (str, bytes)):
        raise DefinitionError("Managed supersession targets must be a collection.", code="tool_metadata_invalid")
    try:
        raw_supersession_targets = tuple(superseded_by_tool_ids)  # type: ignore[arg-type]
    except TypeError as exc:
        raise DefinitionError(
            "Managed supersession targets must be a collection.", code="tool_metadata_invalid"
        ) from exc
    if not all(
        isinstance(item, str) and item.strip() and len(item.strip()) <= MAX_TOOL_ID_LENGTH
        for item in raw_supersession_targets
    ):
        raise DefinitionError("Managed supersession targets are invalid.", code="tool_metadata_invalid")
    normalized_targets = tuple(item.strip() for item in raw_supersession_targets)
    if len(set(normalized_targets)) != len(normalized_targets):
        raise DefinitionError("Managed supersession targets are invalid.", code="tool_metadata_invalid")
    normalized_tool_id = tool_id.strip()
    if normalized_tool_id in normalized_targets:
        raise DefinitionError(
            "Managed tools cannot supersede themselves.",
            code="tool_supersession_self_reference",
            details={"tool_id": normalized_tool_id},
        )

    return {
        "tool_id": normalized_tool_id,
        "effects": normalized_effects,
        "credential_audiences": normalized_audiences,
        "idempotency": idempotency,
        "output_policy": policy,
        "resource_resolver": resource_resolver,
        "superseded_by_tool_ids": frozenset(normalized_targets),
        "shell_review": shell_review,
    }
