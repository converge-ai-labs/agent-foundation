"""Model resolution and narrow provider-history recovery."""
# `_EXPORTS` owns the surface; these imports serve type checkers.

from typing import TYPE_CHECKING, Any

from a13n_harness._exports import load_export

if TYPE_CHECKING:
    from a13n_harness.models.binding import RunModelResolver
    from a13n_harness.models.capability import SelfHealingModelCapability
    from a13n_harness.models.inference import (
        GatewayModelProviderFactory,
        ModelPatch,
        ModelProviderFactory,
        RequestHeadersModel,
        infer_model,
    )
    from a13n_harness.models.request_headers import MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV
    from a13n_harness.models.self_healing import ModelRecoveryRule, SelfHealingModel
    from a13n_harness.models.settings import (
        ModelCharacteristicsAlias,
        ModelCharacteristicsAliasCatalog,
        ModelCharacteristicsTransform,
        ModelSettingsAlias,
        ModelSettingsAliasCatalog,
        ModelSettingsTransform,
        get_model_characteristics_alias_catalog,
        get_model_settings_alias_catalog,
        resolve_model_characteristics,
        resolve_model_settings,
    )
    from a13n_harness.models.transport import (
        DEFAULT_MODEL_HTTP_RETRY_CONFIG,
        DEFAULT_MODEL_HTTP_RETRY_STATUS_CODES,
        ModelHttpRetryConfig,
        create_model_http_client,
    )

_EXPORTS = {
    "a13n_harness.models.binding": ("RunModelResolver",),
    "a13n_harness.models.capability": ("SelfHealingModelCapability",),
    "a13n_harness.models.inference": (
        "GatewayModelProviderFactory",
        "ModelPatch",
        "ModelProviderFactory",
        "RequestHeadersModel",
        "infer_model",
    ),
    "a13n_harness.models.request_headers": ("MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV",),
    "a13n_harness.models.self_healing": ("ModelRecoveryRule", "SelfHealingModel"),
    "a13n_harness.models.settings": (
        "ModelCharacteristicsAlias",
        "ModelCharacteristicsAliasCatalog",
        "ModelCharacteristicsTransform",
        "ModelSettingsAlias",
        "ModelSettingsAliasCatalog",
        "ModelSettingsTransform",
        "get_model_characteristics_alias_catalog",
        "get_model_settings_alias_catalog",
        "resolve_model_characteristics",
        "resolve_model_settings",
    ),
    "a13n_harness.models.transport": (
        "DEFAULT_MODEL_HTTP_RETRY_CONFIG",
        "DEFAULT_MODEL_HTTP_RETRY_STATUS_CODES",
        "ModelHttpRetryConfig",
        "create_model_http_client",
    ),
}

__all__ = [
    "DEFAULT_MODEL_HTTP_RETRY_CONFIG",
    "DEFAULT_MODEL_HTTP_RETRY_STATUS_CODES",
    "MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV",
    "GatewayModelProviderFactory",
    "ModelCharacteristicsAlias",
    "ModelCharacteristicsAliasCatalog",
    "ModelCharacteristicsTransform",
    "ModelHttpRetryConfig",
    "ModelPatch",
    "ModelProviderFactory",
    "ModelRecoveryRule",
    "ModelSettingsAlias",
    "ModelSettingsAliasCatalog",
    "ModelSettingsTransform",
    "RequestHeadersModel",
    "RunModelResolver",
    "SelfHealingModel",
    "SelfHealingModelCapability",
    "create_model_http_client",
    "get_model_characteristics_alias_catalog",
    "get_model_settings_alias_catalog",
    "infer_model",
    "resolve_model_characteristics",
    "resolve_model_settings",
]


def __getattr__(name: str) -> Any:
    return load_export(__name__, globals(), _EXPORTS, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
