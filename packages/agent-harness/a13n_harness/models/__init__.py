"""Model resolution and narrow provider-history recovery."""

from a13n_harness.models.binding import RunModelResolver
from a13n_harness.models.capability import SelfHealingModelCapability
from a13n_harness.models.inference import (
    GatewayModelProviderFactory,
    ModelPatch,
    ModelProviderFactory,
    RequestHeadersModel,
    infer_model,
)
from a13n_harness.models.request_headers import (
    MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV,
    MODEL_REQUEST_X_SESSION_ID_ENABLED_ENV,
)
from a13n_harness.models.self_healing import ModelRecoveryRule, SelfHealingModel
from a13n_harness.models.settings import (
    ModelConfigurationAlias,
    ModelConfigurationAliasCatalog,
    ModelConfigurationTransform,
    ModelSettingsAlias,
    ModelSettingsAliasCatalog,
    ModelSettingsTransform,
    get_model_configuration_alias_catalog,
    get_model_settings_alias_catalog,
    resolve_model_configuration,
    resolve_model_settings,
)

__all__ = [
    "MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV",
    "MODEL_REQUEST_X_SESSION_ID_ENABLED_ENV",
    "GatewayModelProviderFactory",
    "ModelConfigurationAlias",
    "ModelConfigurationAliasCatalog",
    "ModelConfigurationTransform",
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
    "get_model_configuration_alias_catalog",
    "get_model_settings_alias_catalog",
    "infer_model",
    "resolve_model_configuration",
    "resolve_model_settings",
]
