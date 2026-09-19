"""Alibaba Model Studio Provider adapter."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Self

import httpx2
from pydantic import StringConstraints, model_validator

from . import openai_provider
from .credentials import ApiKeyCredential
from .definition import ModelProviderDefinition, bearer_models_request
from .types import ModelConnection, ProviderConfiguration


class DomainType(StrEnum):
    international = "international"
    mainland_china = "mainland_china"


class Region(StrEnum):
    beijing = "cn-beijing"
    singapore = "ap-southeast-1"
    tokyo = "ap-northeast-1"
    frankfurt = "eu-central-1"
    virginia = "us-east-1"


class Config(ProviderConfiguration):
    region: Region
    domain_type: DomainType
    alibaba_workspace_id: (
        Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")] | None
    ) = None

    @model_validator(mode="after")
    def validate_region_domain(self) -> Self:
        if self.region is Region.beijing and self.domain_type is not DomainType.mainland_china:
            raise ValueError("cn-beijing requires mainland_china domain_type")
        if self.region is not Region.beijing and self.domain_type is not DomainType.international:
            raise ValueError("non-Beijing regions require international domain_type")
        if self.region in {Region.tokyo, Region.frankfurt} and self.alibaba_workspace_id is None:
            raise ValueError("the selected Alibaba region requires alibaba_workspace_id")
        return self


def _build_provider(
    provider: ModelConnection[Config, ApiKeyCredential],
    http_client: httpx2.AsyncClient,
    model_api: str,
) -> AlibabaProvider:
    from pydantic_ai.providers.alibaba import AlibabaProvider

    class _AlibabaProvider(openai_provider.ClientEndpointProvider, AlibabaProvider):
        pass

    return openai_provider.build(provider, http_client, model_api, _AlibabaProvider)


def _endpoint(configuration: Mapping[str, object]) -> str:
    region = Region(str(configuration["region"]))
    workspace_id = configuration.get("alibaba_workspace_id")
    if workspace_id is not None:
        return f"https://{workspace_id}.{region.value}.maas.aliyuncs.com/compatible-mode/v1"
    if region is Region.beijing:
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if region is Region.singapore:
        return "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    if region is Region.virginia:
        return "https://dashscope-us.aliyuncs.com/compatible-mode/v1"
    raise ValueError("the selected Alibaba region requires alibaba_workspace_id")


DEFINITION = ModelProviderDefinition(
    type="alibaba_model_studio",
    catalog_providers=("alibaba", "alibaba-cn"),
    setup_url="https://www.alibabacloud.com/help/en/model-studio/get-api-key",
    display_name="Alibaba Model Studio / Qwen",
    configuration_model=Config,
    credential_model=ApiKeyCredential,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint=_endpoint,
    connection_probe=bearer_models_request,
)

if TYPE_CHECKING:
    from pydantic_ai.providers.alibaba import AlibabaProvider
