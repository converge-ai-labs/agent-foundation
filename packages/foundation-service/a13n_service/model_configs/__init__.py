"""Model configuration, provider discovery, and execution snapshot contracts."""

from .domain import (
    CapabilitySource,
    InvokingUserSecretCredential,
    ModelCapabilities,
    ModelConfig,
    ModelConfigCollection,
    ModelConfigCreate,
    ModelConfigPatch,
    ModelConnectionTestResult,
    ModelCredential,
    ModelExecutionObservation,
    ModelExecutionSnapshot,
    NoCredential,
    PrincipalRef,
    WorkspaceSecretCredential,
    new_model_config_id,
)
from .providers import ProviderDefinition, ProviderRegistry, built_in_provider_registry

__all__ = [
    "CapabilitySource",
    "InvokingUserSecretCredential",
    "ModelCapabilities",
    "ModelConfig",
    "ModelConfigCollection",
    "ModelConfigCreate",
    "ModelConfigPatch",
    "ModelConnectionTestResult",
    "ModelCredential",
    "ModelExecutionObservation",
    "ModelExecutionSnapshot",
    "NoCredential",
    "PrincipalRef",
    "ProviderDefinition",
    "ProviderRegistry",
    "WorkspaceSecretCredential",
    "built_in_provider_registry",
    "new_model_config_id",
]
