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
)
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
