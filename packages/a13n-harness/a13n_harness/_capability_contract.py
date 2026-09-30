"""Closed first-party Capability facts and phase-specific composition checks.

This is not an extensible registry. Source admission, built-tree ownership, and
native run replacement are separate checks with deliberately different scopes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP, AbstractCapability, Instrumentation, ResolveModelId

from a13n_harness.capabilities.codeact import CODEACT_CAPABILITY_ID, CodeActCapability
from a13n_harness.capabilities.context import (
    COMPACTION_CAPABILITY_ID,
    FILE_CONTEXT_CAPABILITY_ID,
    HANDOFF_CAPABILITY_ID,
    RUNTIME_CONTEXT_CAPABILITY_ID,
    WORKSPACE_OUTLINE_CAPABILITY_ID,
    CompactionCapability,
    FileContextCapability,
    HandoffCapability,
    RuntimeContextCapability,
    WorkspaceOutlineCapability,
    _FileContextRunCapability,
)
from a13n_harness.capabilities.documents import DOCUMENTS_CAPABILITY_ID, DocumentsCapability
from a13n_harness.capabilities.input import INPUT_CAPABILITY_ID, InputCapability
from a13n_harness.capabilities.interaction import USER_INTERACTION_CAPABILITY_ID, UserInteractionCapability
from a13n_harness.capabilities.lifecycle import (
    LIFECYCLE_EVENT_CAPABILITY_ID,
    LifecycleEventCapability,
    _LifecycleEventActiveCapability,
)
from a13n_harness.capabilities.media import MEDIA_CAPABILITY_ID, MediaCapability
from a13n_harness.capabilities.skills import SKILLS_CAPABILITY_ID, SkillsCapability, _SkillsRunCapability
from a13n_harness.capabilities.steering import STEERING_CAPABILITY_ID, SteeringCapability
from a13n_harness.capabilities.subagents import (
    SUBAGENT_CAPABILITY_ID,
    SubagentCapability,
    _AsyncSubagentCapability,
    _InlineSubagentCapability,
)
from a13n_harness.capabilities.tool_proxy import (
    TOOL_PROXY_CAPABILITY_ID,
    ToolProxyCapability,
    _ToolProxyGroupCapability,
    _ToolProxySurfaceCapability,
)
from a13n_harness.capabilities.web import WEB_CAPABILITY_ID, WebCapability
from a13n_harness.capabilities.working_state import (
    WORKING_STATE_CAPABILITY_ID,
    WorkingStateCapability,
    _WorkingStateRunCapability,
)
from a13n_harness.capability_types import _validate_capability_id
from a13n_harness.context import AgentContext
from a13n_harness.environment.dynamic import (
    DYNAMIC_ENVIRONMENT_CAPABILITY_ID,
    DynamicEnvironmentCapability,
    _DynamicEnvironmentRunCapability,
)
from a13n_harness.errors import DefinitionError
from a13n_harness.filters.integrity import MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID, MessageIntegrityFilterCapability
from a13n_harness.model_context import MODEL_CONTEXT_COORDINATOR_CAPABILITY_ID, ModelContextCoordinatorCapability
from a13n_harness.models.request_headers import MODEL_REQUEST_HEADERS_CAPABILITY_ID, ModelRequestHeadersCapability
from a13n_harness.models.structured_output import (
    STRUCTURED_OUTPUT_AUTO_TOOL_CHOICE_CAPABILITY_ID,
    StructuredOutputAutoToolChoiceCapability,
)
from a13n_harness.pricing import MODEL_COST_CAPABILITY_ID, AbstractModelCostCapability
from a13n_harness.tools.client import CLIENT_TOOLS_CAPABILITY_ID, ClientToolsCapability
from a13n_harness.tools.invocation import TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID, ToolExecutionBoundaryCapability
from a13n_harness.tools.permissions import TOOL_PERMISSIONS_CAPABILITY_ID, ToolPermissionsCapability
from a13n_harness.tools.policy import INVOCATION_POLICY_CAPABILITY_ID, InvocationPolicyCapability
from a13n_harness.tools.surface import TOOL_SURFACE_CAPABILITY_ID, ToolSurfaceCapability
from a13n_harness.usage import USAGE_CAPABILITY_ID, UsageCapability, _RunUsageCapability

_DEFINITION_OWNERS: dict[str, tuple[type[AbstractCapability[AgentContext]], ...]] = {
    CLIENT_TOOLS_CAPABILITY_ID: (ClientToolsCapability,),
    TOOL_PROXY_CAPABILITY_ID: (_ToolProxySurfaceCapability,),
    CODEACT_CAPABILITY_ID: (CodeActCapability,),
    DYNAMIC_ENVIRONMENT_CAPABILITY_ID: (DynamicEnvironmentCapability, _DynamicEnvironmentRunCapability),
    TOOL_PERMISSIONS_CAPABILITY_ID: (ToolPermissionsCapability,),
    RUNTIME_CONTEXT_CAPABILITY_ID: (RuntimeContextCapability,),
    FILE_CONTEXT_CAPABILITY_ID: (FileContextCapability, _FileContextRunCapability),
    WORKSPACE_OUTLINE_CAPABILITY_ID: (WorkspaceOutlineCapability,),
    HANDOFF_CAPABILITY_ID: (HandoffCapability,),
    COMPACTION_CAPABILITY_ID: (CompactionCapability,),
    USER_INTERACTION_CAPABILITY_ID: (UserInteractionCapability,),
    SKILLS_CAPABILITY_ID: (SkillsCapability, _SkillsRunCapability),
    MEDIA_CAPABILITY_ID: (MediaCapability,),
    DOCUMENTS_CAPABILITY_ID: (DocumentsCapability,),
    WEB_CAPABILITY_ID: (WebCapability,),
    WORKING_STATE_CAPABILITY_ID: (WorkingStateCapability, _WorkingStateRunCapability),
    SUBAGENT_CAPABILITY_ID: (SubagentCapability, _InlineSubagentCapability, _AsyncSubagentCapability),
}
_DEFINITION_TYPES = tuple(types[0] for types in _DEFINITION_OWNERS.values())

_BUILT_OWNERS: dict[str, tuple[type[AbstractCapability[AgentContext]], str]] = {
    TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID: (ToolExecutionBoundaryCapability, "tool execution boundary"),
    TOOL_SURFACE_CAPABILITY_ID: (ToolSurfaceCapability, "tool-surface"),
    MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID: (MessageIntegrityFilterCapability, "message-integrity Filter"),
    LIFECYCLE_EVENT_CAPABILITY_ID: (LifecycleEventCapability, "lifecycle event"),
    STEERING_CAPABILITY_ID: (SteeringCapability, "steering"),
    INPUT_CAPABILITY_ID: (InputCapability, "input"),
    MODEL_CONTEXT_COORDINATOR_CAPABILITY_ID: (ModelContextCoordinatorCapability, "model context coordinator"),
    MODEL_REQUEST_HEADERS_CAPABILITY_ID: (ModelRequestHeadersCapability, "model request headers"),
    STRUCTURED_OUTPUT_AUTO_TOOL_CHOICE_CAPABILITY_ID: (
        StructuredOutputAutoToolChoiceCapability,
        "structured-output compatibility",
    ),
    USAGE_CAPABILITY_ID: (UsageCapability, "Usage"),
    MODEL_COST_CAPABILITY_ID: (AbstractModelCostCapability, "model-cost"),
}
_POLYMORPHIC_BUILT_TYPES = (ToolExecutionBoundaryCapability, ToolSurfaceCapability, AbstractModelCostCapability)
_RESERVED_IDS = frozenset((*_BUILT_OWNERS, *_DEFINITION_OWNERS, INVOCATION_POLICY_CAPABILITY_ID))
_RUN_TYPES = (MCP, InvocationPolicyCapability)

# Source admission has a narrower type guard than built ownership. Lifecycle is
# builder-owned and checked at build time; the other excluded types retain ID guards.
_SOURCE_RESERVED_IDS = _RESERVED_IDS - {LIFECYCLE_EVENT_CAPABILITY_ID}
_SOURCE_RESERVED_TYPES = (
    *(_type for _type in _DEFINITION_TYPES if _type is not ToolPermissionsCapability),
    *(
        _type
        for _type, _ in _BUILT_OWNERS.values()
        if _type
        not in (
            LifecycleEventCapability,
            ModelRequestHeadersCapability,
            StructuredOutputAutoToolChoiceCapability,
        )
    ),
    InvocationPolicyCapability,
)


def _reserved_harness_capability_contract() -> tuple[
    tuple[type[AbstractCapability[AgentContext]], ...], frozenset[str]
]:
    capability_types = (
        ToolPermissionsCapability,
        ToolExecutionBoundaryCapability,
        MessageIntegrityFilterCapability,
        LifecycleEventCapability,
        ModelContextCoordinatorCapability,
        InvocationPolicyCapability,
        AbstractModelCostCapability,
        ClientToolsCapability,
        CodeActCapability,
        ToolProxyCapability,
        _ToolProxySurfaceCapability,
        DynamicEnvironmentCapability,
        SubagentCapability,
    )
    names = frozenset(
        {
            *(capability_type.__name__ for capability_type in capability_types),
            *(owner_id for owner_id, types in _DEFINITION_OWNERS.items() if types[0] in capability_types),
            *(owner_id for owner_id, (owner_type, _) in _BUILT_OWNERS.items() if owner_type in capability_types),
            INVOCATION_POLICY_CAPABILITY_ID,
        }
    )
    return capability_types, names


def _validate_built_capability_tree(
    root: AbstractCapability[AgentContext],
    *,
    definition_reserved_ids: frozenset[str],
    expected_instrumentation: Instrumentation | None,
    expected_structured_output_compatibility: bool,
) -> None:
    """Validate stable IDs and protected provenance on the complete Agent-bound tree."""
    leaves: list[AbstractCapability[AgentContext]] = []
    root.apply(leaves.append)
    seen_ids: dict[str, str] = {}
    counts = dict.fromkeys(_BUILT_OWNERS, 0)
    instrumentation_count = 0
    model_resolver_count = 0
    for capability in leaves:
        if not isinstance(capability, AbstractCapability):
            raise DefinitionError(
                "Built Capability trees must contain only AbstractCapability leaves.",
                code="capability_type_invalid",
                details={"source": "built"},
            )
        capability_id = capability.id
        if capability_id is not None:
            capability_id = _validate_capability_id(
                capability_id,
                capability_type=type(capability),
                source="built",
            )
            previous = seen_ids.get(capability_id)
            if previous is not None:
                raise DefinitionError(
                    "Capability IDs must be unique in the built Agent tree.",
                    code="capability_id_duplicate",
                    details={
                        "capability_id": capability_id,
                        "capability_type": type(capability).__name__,
                        "other_capability_type": previous,
                    },
                )
            seen_ids[capability_id] = type(capability).__name__

        if isinstance(capability, Instrumentation):
            instrumentation_count += 1
            if capability is not expected_instrumentation:
                raise DefinitionError(
                    "Agent instrumentation is reserved to HarnessBuilder.",
                    code="instrumentation_owner_conflict",
                    details={"source": "built"},
                )
            continue
        if isinstance(capability, ResolveModelId):
            model_resolver_count += 1
            continue
        matched_owner = False
        for owner_id, (owner_type, label) in _BUILT_OWNERS.items():
            matches = (
                isinstance(capability, owner_type)
                if owner_type in _POLYMORPHIC_BUILT_TYPES
                else type(capability) is owner_type
            )
            if matches:
                counts[owner_id] += 1
                if capability_id != owner_id:
                    raise DefinitionError(
                        f"The mandatory {label} Capability has an invalid ID.",
                        code="capability_scope_invalid",
                    )
                matched_owner = True
                break
        if matched_owner:
            continue

        allowed_definition_reserved = type(capability) in _DEFINITION_TYPES and capability_id in definition_reserved_ids
        if (
            isinstance(capability, InvocationPolicyCapability) or capability_id in _RESERVED_IDS
        ) and not allowed_definition_reserved:
            raise DefinitionError(
                "A reserved Harness Capability is present in the built Agent tree from the wrong source.",
                code="capability_scope_invalid",
                details={
                    "capability_id": capability_id,
                    "capability_type": type(capability).__name__,
                    "source": "built",
                },
            )

    surface_index = next(
        (index for index, capability in enumerate(leaves) if isinstance(capability, ToolSurfaceCapability)),
        None,
    )
    if surface_index is not None:
        for capability in leaves[:surface_index]:
            # Membership adds no global wrapper; its original nodes are visited
            # separately below and retain the same surface-order validation.
            if type(capability) is _ToolProxyGroupCapability:
                continue
            if isinstance(
                capability, ToolExecutionBoundaryCapability | CodeActCapability | _ToolProxySurfaceCapability
            ):
                continue
            if type(capability).get_wrapper_toolset is not AbstractCapability.get_wrapper_toolset:
                raise DefinitionError(
                    "Only ToolProxy, CodeAct, and the tool execution boundary may wrap the mandatory tool surface.",
                    code="tool_surface_order_invalid",
                    details={"capability_type": type(capability).__name__},
                )

    expected_instrumentation_count = 1 if expected_instrumentation is not None else 0
    if instrumentation_count != expected_instrumentation_count:
        raise DefinitionError(
            "The built Agent has an invalid instrumentation owner count.",
            code="instrumentation_owner_conflict",
            details={"source": "built"},
        )
    for owner_id, (_, label) in _BUILT_OWNERS.items():
        if owner_id == STRUCTURED_OUTPUT_AUTO_TOOL_CHOICE_CAPABILITY_ID:
            if counts[owner_id] != int(expected_structured_output_compatibility):
                raise DefinitionError(
                    "The built Agent has an invalid structured-output compatibility Capability count.",
                    code="capability_scope_invalid",
                )
        elif counts[owner_id] != 1:
            raise DefinitionError(
                f"The built Agent must contain exactly one mandatory {label} Capability.",
                code="capability_scope_invalid",
            )
    if model_resolver_count != 1:
        raise DefinitionError(
            "The built Agent must contain exactly one mandatory model resolver Capability.",
            code="capability_scope_invalid",
        )


def _validate_capability_source(
    capabilities: Sequence[AbstractCapability[AgentContext]],
    *,
    source: Literal["definition", "plugin", "run"],
) -> frozenset[str]:
    """Flatten Capability trees and preserve ownership of reserved Harness IDs."""
    leaves: list[AbstractCapability[AgentContext]] = []
    for capability in capabilities:
        if not isinstance(capability, AbstractCapability):
            raise DefinitionError(
                "Configured Capabilities must inherit AbstractCapability.",
                code="capability_type_invalid",
                details={"source": source},
            )
        if isinstance(capability, Instrumentation):
            raise DefinitionError(
                "Agent instrumentation is reserved to HarnessBuilder.",
                code="instrumentation_owner_conflict",
                details={"source": source},
            )
        if source == "run" and type(capability) not in _RUN_TYPES:
            raise DefinitionError(
                "RunBindings.capabilities accepts only documented runtime policy and MCP types.",
                code="capability_scope_invalid",
                details={
                    "capability_id": capability.id,
                    "capability_type": type(capability).__name__,
                    "source": source,
                },
            )
        capability.apply(leaves.append)

    if any(isinstance(capability, Instrumentation) for capability in leaves):
        raise DefinitionError(
            "Agent instrumentation is reserved to HarnessBuilder.",
            code="instrumentation_owner_conflict",
            details={"source": source},
        )

    accepted: set[str] = set()
    for capability in leaves:
        if not isinstance(capability, AbstractCapability):
            raise DefinitionError(
                "Capability trees must contain only AbstractCapability leaves.",
                code="capability_type_invalid",
                details={"source": source},
            )
        allowed = (
            (
                source == "definition"
                and (isinstance(capability, AbstractModelCostCapability) or type(capability) in _DEFINITION_TYPES)
            )
            or (source == "plugin" and type(capability) is _ToolProxySurfaceCapability)
            or (source == "run" and type(capability) in _RUN_TYPES)
        )
        reserved_type = isinstance(capability, _SOURCE_RESERVED_TYPES)
        if reserved_type or capability.id in _SOURCE_RESERVED_IDS:
            if not allowed:
                raise DefinitionError(
                    "A reserved Harness Capability is installed from the wrong source.",
                    code="capability_scope_invalid",
                    details={
                        "capability_id": capability.id,
                        "capability_type": type(capability).__name__,
                        "source": source,
                    },
                )
            if capability.id is not None:
                accepted.add(capability.id)
    return frozenset(accepted)


def _validate_finalized_capability_provenance(ctx: RunContext[AgentContext]) -> None:
    """Reject protected Capability replacement after native for_run finalization."""
    provenance = ctx.deps._capability_provenance
    expected = {
        **{
            owner_id: (types, provenance.definition_ids)
            for owner_id, types in _DEFINITION_OWNERS.items()
            if owner_id != WORKSPACE_OUTLINE_CAPABILITY_ID
        },
        TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID: (
            (ToolExecutionBoundaryCapability,),
            None,
        ),
        MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID: (
            (MessageIntegrityFilterCapability,),
            None,
        ),
        LIFECYCLE_EVENT_CAPABILITY_ID: (
            (_LifecycleEventActiveCapability,),
            None,
        ),
        INVOCATION_POLICY_CAPABILITY_ID: (
            (InvocationPolicyCapability,),
            provenance.run_ids,
        ),
        USAGE_CAPABILITY_ID: (
            (_RunUsageCapability,),
            None,
        ),
        MODEL_COST_CAPABILITY_ID: (
            (AbstractModelCostCapability,),
            None,
        ),
    }
    reserved_types = tuple(capability_type for item in expected.values() for capability_type in item[0])
    for capability_id, capability in ctx.capabilities.items():
        if capability.id is not None:
            _validate_capability_id(
                capability.id,
                capability_type=type(capability),
                source="run_finalized",
            )
        if isinstance(capability, reserved_types) and capability_id not in expected:
            raise DefinitionError(
                "A protected Harness Capability changed its reserved ID during run binding.",
                code="capability_scope_invalid",
                details={
                    "capability_id": capability_id,
                    "capability_type": type(capability).__name__,
                    "source": "run_finalized",
                },
            )

    for capability_id, (expected_types, allowed_ids) in expected.items():
        capability = ctx.capabilities.get(capability_id)
        if capability_id == MODEL_COST_CAPABILITY_ID:
            if not isinstance(capability, AbstractModelCostCapability) or capability_id in provenance.run_ids:
                raise DefinitionError(
                    "The finalized run is missing one build-time model-cost Capability.",
                    code="capability_scope_invalid",
                    details={"capability_id": capability_id, "source": "run_finalized"},
                )
            continue
        if allowed_ids is None:
            if type(capability) is not expected_types[0]:
                raise DefinitionError(
                    "The finalized run is missing an exact mandatory Harness Capability.",
                    code="capability_scope_invalid",
                    details={"capability_id": capability_id, "source": "run_finalized"},
                )
            continue
        if capability is None:
            if allowed_ids is not None and capability_id in allowed_ids:
                raise DefinitionError(
                    "A protected Harness Capability disappeared during run binding.",
                    code="capability_scope_invalid",
                    details={"capability_id": capability_id, "source": "run_finalized"},
                )
            continue
        if type(capability) not in expected_types or allowed_ids is None or capability_id not in allowed_ids:
            raise DefinitionError(
                "A protected Harness Capability changed type or source during run binding.",
                code="capability_scope_invalid",
                details={
                    "capability_id": capability_id,
                    "capability_type": type(capability).__name__,
                    "source": "run_finalized",
                },
            )

    for owner_id, binding, prefix, label in (
        (MEDIA_CAPABILITY_ID, ctx.deps.media_reader, "media", "Media"),
        (DOCUMENTS_CAPABILITY_ID, ctx.deps.document_converter, "documents", "Documents"),
        (WEB_CAPABILITY_ID, ctx.deps.web, "web", "Web"),
    ):
        owner = ctx.capabilities.get(owner_id)
        if binding is not None and owner is None:
            raise DefinitionError(f"A {label} binding requires its definition owner.", code=f"{prefix}_owner_missing")
        if owner is not None and binding is None:
            raise DefinitionError(
                f"{label}Capability requires its RunBindings dependency.", code=f"{prefix}_binding_missing"
            )
    if ctx.deps.task_state is not None and ctx.capabilities.get(WORKING_STATE_CAPABILITY_ID) is None:
        raise DefinitionError("A task-state binding requires its definition owner.", code="task_state_owner_missing")
