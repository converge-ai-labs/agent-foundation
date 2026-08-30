"""Trusted, distribution-owned model provider registry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, StringConstraints, TypeAdapter, model_validator

from .domain import (
    CapabilitySource,
    InvokingUserSecretCredential,
    ModelCapabilities,
    ModelCredential,
    WorkspaceSecretCredential,
)


class ApiProtocol(StrEnum):
    chat_completions = "chat_completions"
    responses = "responses"


class AuthMode(StrEnum):
    bearer = "bearer"
    api_key_header = "api_key_header"


class _ConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _EmptyConfig(_ConnectionConfig):
    pass


class _VertexConfig(_ConnectionConfig):
    project_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
    location: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]


class _AzureOpenAIConfig(_ConnectionConfig):
    resource_endpoint: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
    api_protocol: ApiProtocol = ApiProtocol.responses

    @model_validator(mode="after")
    def normalize_official_endpoint(self) -> _AzureOpenAIConfig:
        try:
            parsed = urlsplit(self.resource_endpoint)
        except ValueError as error:
            raise ValueError("resource_endpoint is not a valid Azure endpoint") from error
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
        normalized = urlunsplit(("https", parsed.netloc, path, parsed.query, parsed.fragment))
        object.__setattr__(self, "resource_endpoint", normalized)
        return self


class _BedrockConfig(_ConnectionConfig):
    region: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(?:-gov)?-[a-z]+-\d$")]


class AlibabaDomainType(StrEnum):
    international = "international"
    mainland_china = "mainland_china"


class AlibabaRegion(StrEnum):
    beijing = "cn-beijing"
    singapore = "ap-southeast-1"
    tokyo = "ap-northeast-1"
    frankfurt = "eu-central-1"
    virginia = "us-east-1"


class _AlibabaConfig(_ConnectionConfig):
    region: AlibabaRegion
    domain_type: AlibabaDomainType
    alibaba_workspace_id: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$"),
        ]
        | None
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


class _OpenAICompatibleConfig(_ConnectionConfig):
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
    api_protocol: ApiProtocol = ApiProtocol.chat_completions
    auth_mode: AuthMode = AuthMode.bearer
    api_key_header_name: (
        Annotated[
            str,
            StringConstraints(pattern=r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$"),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_header_mode(self) -> _OpenAICompatibleConfig:
        if self.auth_mode is AuthMode.api_key_header and self.api_key_header_name is None:
            raise ValueError("api_key_header_name is required for api_key_header auth")
        if self.auth_mode is AuthMode.bearer and self.api_key_header_name is not None:
            raise ValueError("api_key_header_name is accepted only for api_key_header auth")
        if self.api_key_header_name is not None and self.api_key_header_name.lower() in {
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
        }:
            raise ValueError("api_key_header_name is reserved by HTTP")
        return self


class ProviderModelCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_name: str
    display_name: str
    capabilities: ModelCapabilities


class ProviderModelCapabilityEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    display_name: str


class ProviderDefinition(BaseModel):
    """Safe provider metadata exposed by the control plane."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    display_name: str
    credential_schema: dict[str, object]
    connection_schema: dict[str, object]
    model_catalog: tuple[ProviderModelCatalogEntry, ...] = ()
    capability_catalog: tuple[ProviderModelCapabilityEntry, ...] = ()


class ProviderDefinitionCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ProviderDefinition, ...]
    next_cursor: None = None


class ValidatedProviderSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_config: dict[str, object]
    base_url: str | None
    capabilities: ModelCapabilities
    capability_source: CapabilitySource
    adapter_key: str
    adapter_version: str


class _ProviderAdapter:
    def __init__(
        self,
        *,
        key: str,
        display_name: str,
        connection_model: type[_ConnectionConfig],
        credential_sources: tuple[str, ...],
        official_base_url: str | None = None,
        model_catalog: tuple[ProviderModelCatalogEntry, ...] = (),
    ) -> None:
        self.key = key
        self.display_name = display_name
        self.connection_model = connection_model
        self.credential_sources = credential_sources
        self.official_base_url = official_base_url
        self.model_catalog = model_catalog
        self.adapter_key = f"a13n.model.{key}"
        self.adapter_version = "1"

    def public_definition(self) -> ProviderDefinition:
        credential_types = tuple(
            credential_type
            for source, credential_type in (
                ("workspace_secret", WorkspaceSecretCredential),
                ("invoking_user_secret", InvokingUserSecretCredential),
            )
            if source in self.credential_sources
        )
        credential_schema = TypeAdapter(credential_types[0] | credential_types[1]).json_schema()
        return ProviderDefinition(
            key=self.key,
            display_name=self.display_name,
            credential_schema=credential_schema,
            connection_schema=self.connection_model.model_json_schema(),
            model_catalog=self.model_catalog,
            capability_catalog=_CAPABILITY_CATALOG,
        )

    def validate(
        self,
        *,
        model_name: str,
        provider_config: Mapping[str, object],
        credential: ModelCredential,
        capabilities: ModelCapabilities | None,
    ) -> ValidatedProviderSelection:
        if credential.source not in self.credential_sources:
            raise ValueError(f"credential source {credential.source!r} is not supported by provider {self.key!r}")
        normalized_config = self.connection_model.model_validate(dict(provider_config)).model_dump(
            mode="json", exclude_none=True
        )
        base_url = self.official_base_url
        if self.key == "openai_compatible":
            base_url = str(normalized_config["base_url"])
        elif self.key == "azure_openai":
            base_url = str(normalized_config["resource_endpoint"])

        catalog_entry = next((item for item in self.model_catalog if item.model_name == model_name), None)
        resolved_capabilities = capabilities or (
            catalog_entry.capabilities if catalog_entry is not None else ModelCapabilities()
        )
        return ValidatedProviderSelection(
            provider_config=normalized_config,
            base_url=base_url,
            capabilities=resolved_capabilities,
            capability_source=(
                CapabilitySource.manual_override if capabilities is not None else CapabilitySource.catalog
            ),
            adapter_key=self.adapter_key,
            adapter_version=self.adapter_version,
        )


class ProviderRegistry:
    """Immutable allowlist of trusted provider adapters."""

    def __init__(self, adapters: Iterable[_ProviderAdapter]) -> None:
        indexed: dict[str, _ProviderAdapter] = {}
        for adapter in adapters:
            if adapter.key in indexed:
                raise ValueError(f"duplicate provider key {adapter.key!r}")
            indexed[adapter.key] = adapter
        self._adapters = MappingProxyType(indexed)

    def definitions(self) -> tuple[ProviderDefinition, ...]:
        return tuple(adapter.public_definition() for adapter in self._adapters.values())

    def definition(self, provider_type: str) -> ProviderDefinition:
        return self._adapter(provider_type).public_definition()

    def execution_identity(self, provider_type: str) -> tuple[str, str]:
        adapter = self._adapter(provider_type)
        return adapter.adapter_key, adapter.adapter_version

    def validate(
        self,
        *,
        provider_type: str,
        model_name: str,
        provider_config: Mapping[str, object],
        credential: ModelCredential,
        capabilities: ModelCapabilities | None,
    ) -> ValidatedProviderSelection:
        return self._adapter(provider_type).validate(
            model_name=model_name,
            provider_config=provider_config,
            credential=credential,
            capabilities=capabilities,
        )

    def _adapter(self, provider_type: str) -> _ProviderAdapter:
        try:
            return self._adapters[provider_type]
        except KeyError as error:
            raise ValueError(f"unknown provider {provider_type!r}") from error


_CAPABILITY_CATALOG = (
    ProviderModelCapabilityEntry(key="text", display_name="Text input"),
    ProviderModelCapabilityEntry(key="image", display_name="Image input"),
    ProviderModelCapabilityEntry(key="audio", display_name="Audio input"),
    ProviderModelCapabilityEntry(key="video", display_name="Video input"),
    ProviderModelCapabilityEntry(key="tool_calling", display_name="Tool calling"),
    ProviderModelCapabilityEntry(key="structured_output", display_name="Structured output"),
    ProviderModelCapabilityEntry(key="reasoning", display_name="Reasoning"),
)

_SECRET_CREDENTIALS = ("workspace_secret", "invoking_user_secret")


def _catalog(
    model_name: str,
    display_name: str,
    *,
    context_window_tokens: int | None = None,
    max_output_tokens: int | None = None,
    modalities: tuple[str, ...] = ("text",),
    tool_calling: bool | None = True,
    structured_output: bool | None = True,
    reasoning: bool | None = None,
) -> ProviderModelCatalogEntry:
    return ProviderModelCatalogEntry(
        model_name=model_name,
        display_name=display_name,
        capabilities=ModelCapabilities(
            input_modalities=modalities,
            context_window_tokens=context_window_tokens,
            max_output_tokens=max_output_tokens,
            tool_calling=tool_calling,
            structured_output=structured_output,
            reasoning=reasoning,
        ),
    )


def built_in_provider_registry() -> ProviderRegistry:
    """Return the fixed provider composition shipped by the OSS distribution."""

    return ProviderRegistry(
        (
            _ProviderAdapter(
                key="openai",
                display_name="OpenAI",
                connection_model=_EmptyConfig,
                credential_sources=_SECRET_CREDENTIALS,
                official_base_url="https://api.openai.com/v1",
                model_catalog=(
                    _catalog(
                        "gpt-5.6-sol",
                        "GPT-5.6 Sol",
                        context_window_tokens=1_050_000,
                        max_output_tokens=128_000,
                        modalities=("text", "image"),
                        reasoning=True,
                    ),
                    _catalog(
                        "gpt-5.6-terra",
                        "GPT-5.6 Terra",
                        context_window_tokens=1_050_000,
                        max_output_tokens=128_000,
                        modalities=("text", "image"),
                        reasoning=True,
                    ),
                    _catalog(
                        "gpt-5.6-luna",
                        "GPT-5.6 Luna",
                        context_window_tokens=1_050_000,
                        max_output_tokens=128_000,
                        modalities=("text", "image"),
                        reasoning=True,
                    ),
                ),
            ),
            _ProviderAdapter(
                key="anthropic",
                display_name="Anthropic",
                connection_model=_EmptyConfig,
                credential_sources=_SECRET_CREDENTIALS,
                official_base_url="https://api.anthropic.com",
                model_catalog=(
                    _catalog(
                        "claude-fable-5",
                        "Claude Fable 5",
                        context_window_tokens=1_000_000,
                        max_output_tokens=128_000,
                        modalities=("text", "image"),
                        reasoning=True,
                    ),
                    _catalog(
                        "claude-opus-5",
                        "Claude Opus 5",
                        context_window_tokens=1_000_000,
                        max_output_tokens=128_000,
                        modalities=("text", "image"),
                        reasoning=True,
                    ),
                    _catalog(
                        "claude-sonnet-5",
                        "Claude Sonnet 5",
                        context_window_tokens=1_000_000,
                        max_output_tokens=128_000,
                        modalities=("text", "image"),
                        reasoning=True,
                    ),
                    _catalog(
                        "claude-haiku-4-5-20251001",
                        "Claude Haiku 4.5",
                        context_window_tokens=200_000,
                        max_output_tokens=64_000,
                        modalities=("text", "image"),
                        reasoning=True,
                    ),
                ),
            ),
            _ProviderAdapter(
                key="google_gemini",
                display_name="Google Gemini",
                connection_model=_EmptyConfig,
                credential_sources=_SECRET_CREDENTIALS,
                official_base_url="https://generativelanguage.googleapis.com",
                model_catalog=tuple(
                    _catalog(
                        model_name,
                        display_name,
                        modalities=("text", "image", "audio", "video"),
                        reasoning=True,
                    )
                    for model_name, display_name in (
                        ("gemini-3.6-flash", "Gemini 3.6 Flash"),
                        ("gemini-3.5-flash", "Gemini 3.5 Flash"),
                        ("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
                        ("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite"),
                    )
                ),
            ),
            _ProviderAdapter(
                key="google_vertex",
                display_name="Google Vertex AI",
                connection_model=_VertexConfig,
                credential_sources=_SECRET_CREDENTIALS,
            ),
            _ProviderAdapter(
                key="azure_openai",
                display_name="Azure OpenAI",
                connection_model=_AzureOpenAIConfig,
                credential_sources=_SECRET_CREDENTIALS,
            ),
            _ProviderAdapter(
                key="aws_bedrock",
                display_name="AWS Bedrock",
                connection_model=_BedrockConfig,
                credential_sources=_SECRET_CREDENTIALS,
            ),
            _ProviderAdapter(
                key="alibaba_model_studio",
                display_name="Alibaba Model Studio / Qwen",
                connection_model=_AlibabaConfig,
                credential_sources=_SECRET_CREDENTIALS,
                model_catalog=tuple(
                    _catalog(
                        model_name,
                        display_name,
                        context_window_tokens=1_000_000,
                        reasoning=True,
                    )
                    for model_name, display_name in (
                        ("qwen3.8-max", "Qwen 3.8 Max"),
                        ("qwen3.7-plus", "Qwen 3.7 Plus"),
                        ("qwen3.7-flash", "Qwen 3.7 Flash"),
                    )
                ),
            ),
            _ProviderAdapter(
                key="deepseek",
                display_name="DeepSeek",
                connection_model=_EmptyConfig,
                credential_sources=_SECRET_CREDENTIALS,
                official_base_url="https://api.deepseek.com",
                model_catalog=(
                    _catalog(
                        "deepseek-v4-pro",
                        "DeepSeek V4 Pro",
                        context_window_tokens=1_000_000,
                        max_output_tokens=384_000,
                        reasoning=True,
                    ),
                    _catalog(
                        "deepseek-v4-flash",
                        "DeepSeek V4 Flash",
                        context_window_tokens=1_000_000,
                        max_output_tokens=384_000,
                        reasoning=True,
                    ),
                ),
            ),
            _ProviderAdapter(
                key="moonshot",
                display_name="Moonshot / Kimi",
                connection_model=_EmptyConfig,
                credential_sources=_SECRET_CREDENTIALS,
                official_base_url="https://api.moonshot.cn/v1",
                model_catalog=(
                    _catalog(
                        "kimi-k2.5",
                        "Kimi K2.5",
                        modalities=("text", "image"),
                        tool_calling=None,
                        structured_output=None,
                        reasoning=None,
                    ),
                ),
            ),
            _ProviderAdapter(
                key="zhipu",
                display_name="Zhipu / GLM",
                connection_model=_EmptyConfig,
                credential_sources=_SECRET_CREDENTIALS,
                official_base_url="https://open.bigmodel.cn/api/paas/v4",
                model_catalog=(
                    _catalog(
                        "glm-5.2",
                        "GLM-5.2",
                        context_window_tokens=1_000_000,
                        max_output_tokens=128_000,
                        reasoning=True,
                    ),
                    _catalog(
                        "glm-5",
                        "GLM-5",
                        context_window_tokens=200_000,
                        max_output_tokens=128_000,
                        reasoning=True,
                    ),
                ),
            ),
            _ProviderAdapter(
                key="openai_compatible",
                display_name="OpenAI-Compatible",
                connection_model=_OpenAICompatibleConfig,
                credential_sources=_SECRET_CREDENTIALS,
            ),
        )
    )


def alibaba_base_url(config: Mapping[str, object]) -> str:
    """Derive the trusted OpenAI-compatible Model Studio endpoint."""

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
