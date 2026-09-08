"""AWS Bedrock Provider adapter."""

from typing import Annotated, Any

import httpx2
from anyio import move_on_after, to_thread
from botocore.config import Config as ClientConfig
from botocore.session import get_session
from openai import AsyncBedrockOpenAI
from pydantic import StringConstraints
from pydantic_ai.providers import Provider
from pydantic_ai.providers.bedrock import BedrockProvider
from pydantic_ai.providers.bedrock_mantle import BedrockMantleProvider

from ..credentials import parse_aws_credentials
from .base import ProviderIntegration, require_credential
from .types import CredentialFormat, ProviderConfiguration, RuntimeProvider


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    model_api: str,
) -> Provider[Any]:
    credentials = parse_aws_credentials(require_credential(provider))
    region = str(provider.configuration["region"])
    if model_api == "bedrock.converse":
        suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
        client = get_session().create_client(
            "bedrock-runtime",
            region_name=region,
            endpoint_url=f"https://bedrock-runtime.{region}.{suffix}",
            config=ClientConfig(retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=600),
            **credentials.model_dump(),
        )
        return _BedrockProvider(bedrock_client=client)
    if model_api in {"bedrock_mantle.responses", "bedrock_mantle.chat_completions"}:
        client = AsyncBedrockOpenAI(
            aws_region=region,
            base_url=f"https://bedrock-mantle.{region}.api.aws/openai/v1",
            http_client=http_client,
            max_retries=0,
            **credentials.model_dump(),
        )
        return BedrockMantleProvider(openai_client=client)
    raise ValueError(f"unsupported Bedrock calling API {model_api!r}")


class _BedrockProvider(BedrockProvider):
    async def __aexit__(self, *_: object) -> None:
        with move_on_after(5, shield=True):
            await to_thread.run_sync(self.client.close, abandon_on_cancel=True)


class Config(ProviderConfiguration):
    region: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(?:-gov)?-[a-z]+-\d$")]


INTEGRATION = ProviderIntegration(
    type="aws_bedrock",
    display_name="AWS Bedrock",
    configuration_model=Config,
    supported_model_apis=(
        "bedrock.converse",
        "bedrock_mantle.responses",
        "bedrock_mantle.chat_completions",
    ),
    build_provider=_build_provider,
    credential_format=CredentialFormat.aws_credentials_json,
)
