"""Managed invocation, deferred continuation, and client-tool contracts."""

from typing import TYPE_CHECKING, Any

from a13n_harness._exports import load_export

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
    from a13n_harness.tools.deferred import DeferredToolResume
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
    "APPROVAL_PRESENTATION_KEY": ("a13n_harness.tools.approval", "APPROVAL_PRESENTATION_KEY"),
    "ApprovalPresentation": ("a13n_harness.tools.approval", "ApprovalPresentation"),
    "ApprovalSource": ("a13n_harness.tools.approval", "ApprovalSource"),
    "ToolApprovalContext": ("a13n_harness.tools.approval", "ToolApprovalContext"),
    "ClientToolDefinition": ("a13n_harness.tools.client", "ClientToolDefinition"),
    "ClientToolsCapability": ("a13n_harness.tools.client", "ClientToolsCapability"),
    "ClientToolsetDefinition": ("a13n_harness.tools.client", "ClientToolsetDefinition"),
    "ClientToolsSpec": ("a13n_harness.tools.client", "ClientToolsSpec"),
    "DeferredToolResume": ("a13n_harness.tools.deferred", "DeferredToolResume"),
    "ToolIdentity": ("a13n_harness.tools.identity", "ToolIdentity"),
    "ToolIdentityToolset": ("a13n_harness.tools.identity", "ToolIdentityToolset"),
    "ToolPermissionMode": ("a13n_harness.tools.identity", "ToolPermissionMode"),
    "source_tool_id": ("a13n_harness.tools.identity", "source_tool_id"),
    "source_tool_prefix": ("a13n_harness.tools.identity", "source_tool_prefix"),
    "ManagedToolProviderError": ("a13n_harness.tools.invocation", "ManagedToolProviderError"),
    "current_invocation_scope": ("a13n_harness.tools.invocation", "current_invocation_scope"),
    "HARNESS_TOOL_METADATA_KEY": ("a13n_harness.tools.metadata", "HARNESS_TOOL_METADATA_KEY"),
    "RECOVERY_RETRY_SAFE_METADATA_KEY": ("a13n_harness.tools.metadata", "RECOVERY_RETRY_SAFE_METADATA_KEY"),
    "CanonicalResource": ("a13n_harness.tools.metadata", "CanonicalResource"),
    "HarnessTool": ("a13n_harness.tools.metadata", "HarnessTool"),
    "HarnessToolMetadata": ("a13n_harness.tools.metadata", "HarnessToolMetadata"),
    "IdempotencySemantics": ("a13n_harness.tools.metadata", "IdempotencySemantics"),
    "ToolEffect": ("a13n_harness.tools.metadata", "ToolEffect"),
    "ToolOutputPolicy": ("a13n_harness.tools.metadata", "ToolOutputPolicy"),
    "ToolResourceResolver": ("a13n_harness.tools.metadata", "ToolResourceResolver"),
    "recovery_retryable": ("a13n_harness.tools.metadata", "recovery_retryable"),
    "ToolPermissions": ("a13n_harness.tools.permissions", "ToolPermissions"),
    "ToolPermissionsCapability": ("a13n_harness.tools.permissions", "ToolPermissionsCapability"),
    "ToolPermissionSetting": ("a13n_harness.tools.permissions", "ToolPermissionSetting"),
    "ApprovalVerifier": ("a13n_harness.tools.policy", "ApprovalVerifier"),
    "CredentialBroker": ("a13n_harness.tools.policy", "CredentialBroker"),
    "CredentialLease": ("a13n_harness.tools.policy", "CredentialLease"),
    "InvocationGrantBroker": ("a13n_harness.tools.policy", "InvocationGrantBroker"),
    "InvocationGrantRef": ("a13n_harness.tools.policy", "InvocationGrantRef"),
    "InvocationPolicyCapability": ("a13n_harness.tools.policy", "InvocationPolicyCapability"),
    "InvocationPolicyDecision": ("a13n_harness.tools.policy", "InvocationPolicyDecision"),
    "InvocationPolicyEvaluator": ("a13n_harness.tools.policy", "InvocationPolicyEvaluator"),
    "InvocationScope": ("a13n_harness.tools.policy", "InvocationScope"),
    "ToolInvocationContext": ("a13n_harness.tools.policy", "ToolInvocationContext"),
}


def __getattr__(name: str) -> Any:
    return load_export(__name__, globals(), _EXPORTS, name)


__all__ = [
    "APPROVAL_PRESENTATION_KEY",
    "HARNESS_TOOL_METADATA_KEY",
    "RECOVERY_RETRY_SAFE_METADATA_KEY",
    "ApprovalPresentation",
    "ApprovalSource",
    "ApprovalVerifier",
    "CanonicalResource",
    "ClientToolDefinition",
    "ClientToolsCapability",
    "ClientToolsSpec",
    "ClientToolsetDefinition",
    "CredentialBroker",
    "CredentialLease",
    "DeferredToolResume",
    "HarnessTool",
    "HarnessToolMetadata",
    "IdempotencySemantics",
    "InvocationGrantBroker",
    "InvocationGrantRef",
    "InvocationPolicyCapability",
    "InvocationPolicyDecision",
    "InvocationPolicyEvaluator",
    "InvocationScope",
    "ManagedToolProviderError",
    "ToolApprovalContext",
    "ToolEffect",
    "ToolIdentity",
    "ToolIdentityToolset",
    "ToolInvocationContext",
    "ToolOutputPolicy",
    "ToolPermissionMode",
    "ToolPermissionSetting",
    "ToolPermissions",
    "ToolPermissionsCapability",
    "ToolResourceResolver",
    "current_invocation_scope",
    "recovery_retryable",
    "source_tool_id",
    "source_tool_prefix",
]
