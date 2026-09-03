"""AWS Bedrock Provider adapter."""

from typing import Annotated, Any

import httpx2
from pydantic import StringConstraints
from pydantic_ai.providers import Provider
from pydantic_ai.providers.bedrock import BedrockProvider
from pydantic_ai.providers.bedrock_mantle import BedrockMantleProvider

from ..credentials import parse_aws_credentials
from .base import ProviderIntegration, require_credential
from .types import CredentialFormat, ProviderConfig, RuntimeProvider


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    pydantic_provider_name: str,
) -> Provider[Any]:
    del http_client
    credentials = parse_aws_credentials(require_credential(provider))
    region = str(provider.config["region"])
    if pydantic_provider_name == "bedrock":
        return BedrockProvider(region_name=region, **credentials.model_dump())
    if pydantic_provider_name == "bedrock-mantle":
        return BedrockMantleProvider(region_name=region, **credentials.model_dump())
    raise ValueError(f"unsupported Pydantic Provider {pydantic_provider_name!r}")


class Config(ProviderConfig):
    region: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(?:-gov)?-[a-z]+-\d$")]


INTEGRATION = ProviderIntegration(
    key="aws_bedrock",
    display_name="AWS Bedrock",
    config_model=Config,
    supported_model_apis=(
        "bedrock.converse",
        "bedrock_mantle.responses",
        "bedrock_mantle.chat_completions",
    ),
    build_provider=_build_provider,
    credential_format=CredentialFormat.aws_credentials_json,
)
