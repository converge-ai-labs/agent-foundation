"""Managed invocation, deferred continuation, and client-tool contracts."""
# ruff: noqa: F401  # `_EXPORTS` owns the surface; these imports serve type checkers.

from typing import TYPE_CHECKING, Any

from a13n_harness._exports import exported_names, load_export

if TYPE_CHECKING:
    from a13n_harness.tools.approval import (
        APPROVAL_PRESENTATION_KEY,
        ApprovalPresentation,
        ApprovalSource,
        ToolApprovalContext,
    )
    from a13n_harness.tools.client import (
        ClientToolDefinition,
        ClientToolsCapability,
        ClientToolsetDefinition,
        ClientToolsSpec,
    )
    from a13n_harness.tools.deferred import DeferredToolResume, InlineSubagentDeferredResults
    from a13n_harness.tools.identity import (
        ToolIdentity,
        ToolIdentityToolset,
        ToolPermissionMode,
        source_tool_id,
        source_tool_prefix,
    )
    from a13n_harness.tools.invocation import ManagedToolProviderError, current_invocation_scope
    from a13n_harness.tools.metadata import (
        HARNESS_TOOL_METADATA_KEY,
        RECOVERY_RETRY_SAFE_METADATA_KEY,
        CanonicalResource,
        HarnessTool,
        HarnessToolMetadata,
        IdempotencySemantics,
        ToolEffect,
        ToolOutputPolicy,
        ToolResourceResolver,
        recovery_retryable,
    )
    from a13n_harness.tools.permissions import ToolPermissions, ToolPermissionsCapability, ToolPermissionSetting
    from a13n_harness.tools.policy import (
        ApprovalVerifier,
        CredentialBroker,
        CredentialLease,
        InvocationGrantBroker,
        InvocationGrantRef,
        InvocationPolicyCapability,
        InvocationPolicyDecision,
        InvocationPolicyEvaluator,
        InvocationScope,
        ToolInvocationContext,
    )

_EXPORTS = {
    "a13n_harness.tools.approval": (
        "APPROVAL_PRESENTATION_KEY",
        "ApprovalPresentation",
        "ApprovalSource",
        "ToolApprovalContext",
    ),
    "a13n_harness.tools.client": (
        "ClientToolDefinition",
        "ClientToolsCapability",
        "ClientToolsSpec",
        "ClientToolsetDefinition",
    ),
    "a13n_harness.tools.deferred": ("DeferredToolResume", "InlineSubagentDeferredResults"),
    "a13n_harness.tools.identity": (
        "ToolIdentity",
        "ToolIdentityToolset",
        "ToolPermissionMode",
        "source_tool_id",
        "source_tool_prefix",
    ),
    "a13n_harness.tools.invocation": (
        "ManagedToolProviderError",
        "current_invocation_scope",
    ),
    "a13n_harness.tools.metadata": (
        "CanonicalResource",
        "HARNESS_TOOL_METADATA_KEY",
        "HarnessTool",
        "HarnessToolMetadata",
        "IdempotencySemantics",
        "RECOVERY_RETRY_SAFE_METADATA_KEY",
        "ToolEffect",
        "ToolOutputPolicy",
        "ToolResourceResolver",
        "recovery_retryable",
    ),
    "a13n_harness.tools.permissions": (
        "ToolPermissionSetting",
        "ToolPermissions",
        "ToolPermissionsCapability",
    ),
    "a13n_harness.tools.policy": (
        "ApprovalVerifier",
        "CredentialBroker",
        "CredentialLease",
        "InvocationGrantBroker",
        "InvocationGrantRef",
        "InvocationPolicyCapability",
        "InvocationPolicyDecision",
        "InvocationPolicyEvaluator",
        "InvocationScope",
        "ToolInvocationContext",
    ),
}


def __getattr__(name: str) -> Any:
    return load_export(__name__, globals(), _EXPORTS, name)


__all__ = exported_names(_EXPORTS)  # pyright: ignore[reportUnsupportedDunderAll]
