"""Trusted, distribution-owned model Provider type registry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from .domain import BoundedName, ModelApiConfig, UpstreamModel


class AuthMode(StrEnum):
    none = "none"
    bearer = "bearer"
    api_key_header = "api_key_header"


class CredentialFormat(StrEnum):
    api_key = "api_key"
    aws_credentials_json = "aws_credentials_json"
    google_service_account_json = "google_service_account_json"


class _ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _EmptyConfig(_ProviderConfig):
    pass


class _VertexConfig(_ProviderConfig):
    project_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
    location: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]


class _AzureOpenAIConfig(_ProviderConfig):
    resource_endpoint: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
    api_version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] | None = None

    @model_validator(mode="after")
    def normalize_endpoint(self) -> _AzureOpenAIConfig:
        object.__setattr__(self, "resource_endpoint", _official_azure_endpoint(self.resource_endpoint))
        return self


class _BedrockConfig(_ProviderConfig):
    region: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(?:-gov)?-[a-z]+-\d$")]


class _OllamaConfig(_ProviderConfig):
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


class AlibabaDomainType(StrEnum):
    international = "international"
    mainland_china = "mainland_china"


class AlibabaRegion(StrEnum):
    beijing = "cn-beijing"
    singapore = "ap-southeast-1"
    tokyo = "ap-northeast-1"
    frankfurt = "eu-central-1"
    virginia = "us-east-1"


class _AlibabaConfig(_ProviderConfig):
    region: AlibabaRegion
    domain_type: AlibabaDomainType
    alibaba_workspace_id: (
        Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")] | None
    ) = None

    @model_validator(mode="after")
    def validate_region_domain(self) -> _AlibabaConfig:
        if self.region is AlibabaRegion.beijing and self.domain_type is not AlibabaDomainType.mainland_china:
            raise ValueError("cn-beijing requires mainland_china domain_type")
        if self.region is not AlibabaRegion.beijing and self.domain_type is not AlibabaDomainType.international:
            raise ValueError("non-Beijing regions require international domain_type")
        if self.region in {AlibabaRegion.tokyo, AlibabaRegion.frankfurt} and self.alibaba_workspace_id is None:
            raise ValueError("the selected Alibaba region requires alibaba_workspace_id")
        return self


class _OpenAICompatibleConfig(_ProviderConfig):
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
    auth_mode: AuthMode = AuthMode.bearer
    api_key_header_name: Annotated[str, StringConstraints(pattern=r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")] | None = None

    @model_validator(mode="after")
    def validate_header_mode(self) -> _OpenAICompatibleConfig:
        if self.auth_mode is AuthMode.api_key_header and self.api_key_header_name is None:
            raise ValueError("api_key_header_name is required for api_key_header auth")
        if self.auth_mode is not AuthMode.api_key_header and self.api_key_header_name is not None:
            raise ValueError("api_key_header_name is accepted only for api_key_header auth")
        if self.api_key_header_name is not None and self.api_key_header_name.lower() in _RESERVED_HEADERS:
            raise ValueError("api_key_header_name is reserved by HTTP")
        return self


class DiscoveredModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    upstream_model: UpstreamModel
    display_name: BoundedName | None = None
    suggested_model_apis: tuple[ModelApiConfig, ...]


class DiscoveredModelCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[DiscoveredModel, ...]


class ModelProviderTypeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    display_name: str
    config_schema: dict[str, object]
    credential_schema: dict[str, object]
    supported_model_apis: tuple[str, ...]
    supports_model_discovery: bool


class ModelProviderTypeDefinitionCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ModelProviderTypeDefinition, ...]
    next_cursor: None = None


class ValidatedProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config: dict[str, object]
    endpoint: str | None


class _ProviderType:
    def __init__(
        self,
        *,
        key: str,
        display_name: str,
        config_model: type[_ProviderConfig],
        supported_model_apis: tuple[str, ...],
        credential_format: CredentialFormat | None = CredentialFormat.api_key,
        credential_required: bool = True,
        official_endpoint: str | None = None,
        supports_model_discovery: bool = False,
    ) -> None:
        self.key = key
        self.display_name = display_name
        self.config_model = config_model
        self.supported_model_apis = supported_model_apis
        self.credential_format = credential_format
        self.credential_required = credential_required
        self.official_endpoint = official_endpoint
        self.supports_model_discovery = supports_model_discovery

    def definition(self) -> ModelProviderTypeDefinition:
        credential_schema: dict[str, object] = {"type": "null"}
        if self.credential_format is not None:
            credential_schema = {
                "type": "string",
                "format": "password",
                "writeOnly": True,
                "x-a13n-credential-format": self.credential_format.value,
            }
        return ModelProviderTypeDefinition(
            key=self.key,
            display_name=self.display_name,
            config_schema=self.config_model.model_json_schema(),
            credential_schema=credential_schema,
            supported_model_apis=self.supported_model_apis,
            supports_model_discovery=self.supports_model_discovery,
        )

    def validate_config(self, config: Mapping[str, object], *, credential_configured: bool) -> ValidatedProviderConfig:
        if self.credential_required and not credential_configured:
            raise ValueError("the provider credential is required")
        if self.credential_format is None and credential_configured:
            raise ValueError("the provider does not accept a credential")
        normalized = self.config_model.model_validate(dict(config)).model_dump(mode="json", exclude_none=True)
        if self.key == "openai_compatible":
            unauthenticated = normalized["auth_mode"] == AuthMode.none.value
            if unauthenticated and credential_configured:
                raise ValueError("the unauthenticated mode does not accept a credential")
            if not unauthenticated and not credential_configured:
                raise ValueError("the configured authentication mode requires a credential")
        endpoint = self.official_endpoint
        if self.key in {"openai_compatible", "ollama"}:
            endpoint = str(normalized["base_url"])
        elif self.key == "azure_openai":
            endpoint = str(normalized["resource_endpoint"])
        elif self.key == "alibaba_model_studio":
            endpoint = alibaba_base_url(normalized)
        return ValidatedProviderConfig(config=normalized, endpoint=endpoint)


class ProviderRegistry:
    """Immutable allowlist of trusted Provider types and native Model API bindings."""

    def __init__(self, provider_types: Iterable[_ProviderType]) -> None:
        indexed: dict[str, _ProviderType] = {}
        for provider_type in provider_types:
            if provider_type.key in indexed:
                raise ValueError(f"duplicate provider type {provider_type.key!r}")
            indexed[provider_type.key] = provider_type
        self._provider_types = MappingProxyType(indexed)

    def definitions(self) -> tuple[ModelProviderTypeDefinition, ...]:
        return tuple(item.definition() for item in self._provider_types.values())

    def definition(self, provider_type: str) -> ModelProviderTypeDefinition:
        return self._require(provider_type).definition()

    def validate_provider(
        self, provider_type: str, config: Mapping[str, object], *, credential_configured: bool
    ) -> ValidatedProviderConfig:
        return self._require(provider_type).validate_config(config, credential_configured=credential_configured)

    def validate_model_apis(self, provider_type: str, model_apis: Sequence[ModelApiConfig]) -> None:
        allowed = set(self._require(provider_type).supported_model_apis)
        unsupported = sorted(item.api for item in model_apis if item.api not in allowed)
        if unsupported:
            raise ValueError(f"unsupported model APIs: {', '.join(unsupported)}")

    def validate_model_api(self, provider_type: str, model_api: str) -> None:
        allowed = self._require(provider_type).supported_model_apis
        if model_api not in allowed:
            raise ValueError(f"unsupported model API: {model_api}")

    def credential_format(self, provider_type: str) -> CredentialFormat | None:
        return self._require(provider_type).credential_format

    def _require(self, provider_type: str) -> _ProviderType:
        try:
            return self._provider_types[provider_type]
        except KeyError as error:
            raise ValueError(f"unknown provider type {provider_type!r}") from error


def built_in_provider_registry() -> ProviderRegistry:
    openai_apis = ("openai.responses", "openai.chat_completions")
    return ProviderRegistry(
        (
            _ProviderType(
                key="openai",
                display_name="OpenAI",
                config_model=_EmptyConfig,
                supported_model_apis=openai_apis,
                official_endpoint="https://api.openai.com/v1",
                supports_model_discovery=True,
            ),
            _ProviderType(
                key="anthropic",
                display_name="Anthropic",
                config_model=_EmptyConfig,
                supported_model_apis=("anthropic.messages",),
                official_endpoint="https://api.anthropic.com",
                supports_model_discovery=True,
            ),
            _ProviderType(
                key="google_gemini",
                display_name="Google Gemini",
                config_model=_EmptyConfig,
                supported_model_apis=("google.generate_content",),
                official_endpoint="https://generativelanguage.googleapis.com",
                supports_model_discovery=True,
            ),
            _ProviderType(
                key="google_vertex",
                display_name="Google Vertex AI",
                config_model=_VertexConfig,
                supported_model_apis=("google.generate_content",),
                credential_format=CredentialFormat.google_service_account_json,
            ),
            _ProviderType(
                key="azure_openai",
                display_name="Azure OpenAI",
                config_model=_AzureOpenAIConfig,
                supported_model_apis=openai_apis,
                supports_model_discovery=True,
            ),
            _ProviderType(
                key="aws_bedrock",
                display_name="AWS Bedrock",
                config_model=_BedrockConfig,
                supported_model_apis=(
                    "bedrock.converse",
                    "bedrock_mantle.responses",
                    "bedrock_mantle.chat_completions",
                ),
                credential_format=CredentialFormat.aws_credentials_json,
            ),
            _ProviderType(
                key="openrouter",
                display_name="OpenRouter",
                config_model=_EmptyConfig,
                supported_model_apis=("openrouter.chat_completions",),
                official_endpoint="https://openrouter.ai/api/v1",
                supports_model_discovery=True,
            ),
            _ProviderType(
                key="ollama",
                display_name="Ollama",
                config_model=_OllamaConfig,
                supported_model_apis=("ollama.chat_completions",),
                credential_format=None,
                credential_required=False,
                supports_model_discovery=True,
            ),
            _ProviderType(
                key="alibaba_model_studio",
                display_name="Alibaba Model Studio / Qwen",
                config_model=_AlibabaConfig,
                supported_model_apis=("openai.chat_completions",),
                supports_model_discovery=True,
            ),
            _ProviderType(
                key="deepseek",
                display_name="DeepSeek",
                config_model=_EmptyConfig,
                supported_model_apis=("openai.chat_completions",),
                official_endpoint="https://api.deepseek.com",
                supports_model_discovery=True,
            ),
            _ProviderType(
                key="moonshot",
                display_name="Moonshot / Kimi",
                config_model=_EmptyConfig,
                supported_model_apis=("openai.chat_completions",),
                official_endpoint="https://api.moonshot.cn/v1",
                supports_model_discovery=True,
            ),
            _ProviderType(
                key="zhipu",
                display_name="Zhipu / GLM",
                config_model=_EmptyConfig,
                supported_model_apis=("openai.chat_completions",),
                official_endpoint="https://open.bigmodel.cn/api/paas/v4",
                supports_model_discovery=True,
            ),
            _ProviderType(
                key="openai_compatible",
                display_name="OpenAI-Compatible",
                config_model=_OpenAICompatibleConfig,
                supported_model_apis=openai_apis,
                credential_required=False,
                supports_model_discovery=True,
            ),
        )
    )


def alibaba_base_url(config: Mapping[str, object]) -> str:
    region = AlibabaRegion(str(config["region"]))
    workspace_id = config.get("alibaba_workspace_id")
    if workspace_id is not None:
        return f"https://{workspace_id}.{region.value}.maas.aliyuncs.com/compatible-mode/v1"
    if region is AlibabaRegion.beijing:
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if region is AlibabaRegion.singapore:
        return "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    if region is AlibabaRegion.virginia:
        return "https://dashscope-us.aliyuncs.com/compatible-mode/v1"
    raise ValueError("the selected Alibaba region requires alibaba_workspace_id")


def _official_azure_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("resource_endpoint must use the official Azure HTTPS endpoint")
    if hostname.endswith(".openai.azure.com"):
        path = parsed.path.rstrip("/")
        if path not in {"", "/openai/v1"}:
            raise ValueError("Azure OpenAI resource_endpoint must select the v1 API")
        path = "/openai/v1"
    elif hostname.endswith(".models.ai.azure.com"):
        path = parsed.path.rstrip("/")
        if path:
            raise ValueError("Azure AI model resource_endpoint must not contain a path")
    else:
        raise ValueError("resource_endpoint must use an official Azure model domain")
    return urlunsplit(("https", parsed.netloc, path, "", ""))


_RESERVED_HEADERS = {
    "connection",
    "content-length",
    "cookie",
    "host",
    "proxy-authorization",
    "set-cookie",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
