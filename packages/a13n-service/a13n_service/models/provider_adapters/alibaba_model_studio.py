"""Alibaba Model Studio Provider adapter."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Self

import httpx2
from pydantic import StringConstraints, model_validator
from pydantic_ai.providers.alibaba import AlibabaProvider

from . import openai_provider
from .base import ProviderIntegration, bearer_models_request
from .openai_provider import openai_style_discovery
from .types import ProviderConfiguration, RuntimeProvider


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
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    model_api: str,
) -> AlibabaProvider:
    return openai_provider.build(provider, http_client, model_api, AlibabaProvider)


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


INTEGRATION = ProviderIntegration(
    type="alibaba_model_studio",
    display_name="Alibaba Model Studio / Qwen",
    configuration_model=Config,
    supported_model_apis=("openai.chat_completions",),
    build_provider=_build_provider,
    endpoint=_endpoint,
    model_discovery=openai_style_discovery(bearer_models_request),
)
