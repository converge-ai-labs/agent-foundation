"""AWS Bedrock Provider adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import httpx2
from anyio import move_on_after, to_thread
from pydantic import Field, StringConstraints

from .credentials import AwsCredentials
from .definition import ModelProviderDefinition
from .types import ModelConnection, ProviderConfiguration


def _build_provider(
    provider: ModelConnection[Config, AwsCredentials],
    http_client: httpx2.AsyncClient,
    model_api: str,
) -> Provider[Any]:
    from botocore.config import Config as ClientConfig
    from botocore.session import get_session
    from openai import AsyncBedrockOpenAI
    from pydantic_ai.providers.bedrock import BedrockProvider
    from pydantic_ai.providers.bedrock_mantle import BedrockMantleProvider

    class _BedrockProvider(BedrockProvider):
        async def __aexit__(self, *_: object) -> None:
            with move_on_after(5, shield=True):
                await to_thread.run_sync(self.client.close, abandon_on_cancel=True)

    assert provider.credential is not None
    credentials = provider.credential
    region = str(provider.configuration.region)
    if model_api == "bedrock.converse":
        suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
        client = get_session().create_client(
            "bedrock-runtime",
            region_name=region,
            endpoint_url=provider.endpoint or f"https://bedrock-runtime.{region}.{suffix}",
            config=ClientConfig(retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=600),
            **credentials.native_values(),
        )
        if provider.extra_headers:

            def add_headers(params: dict[str, Any], **_: Any) -> None:
                params["headers"].update(provider.extra_headers)

            for operation in ("Converse", "ConverseStream", "CountTokens"):
                client.meta.events.register(f"before-call.bedrock-runtime.{operation}", add_headers)
        return _BedrockProvider(bedrock_client=client)
    if model_api in {"bedrock_mantle.responses", "bedrock_mantle.chat_completions"}:
        client = AsyncBedrockOpenAI(
            aws_region=region,
            base_url=str(
                provider.configuration.mantle_base_url or f"https://bedrock-mantle.{region}.api.aws/openai/v1"
            ),
            default_headers=provider.extra_headers,
            http_client=http_client,
            max_retries=0,
            **credentials.native_values(),
        )
        return BedrockMantleProvider(openai_client=client)
    raise ValueError(f"unsupported Bedrock calling API {model_api!r}")


class Config(ProviderConfiguration):
    region: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(?:-gov)?-[a-z]+-\d$")]
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)] | None = Field(
        default=None, title="Converse base URL", description="Override the Bedrock Runtime endpoint."
    )
    mantle_base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)] | None = (
        Field(
            default=None,
            title="Mantle base URL",
            description="Override the Mantle endpoint. The SDK selects its /v1 or /openai/v1 suffix for each model.",
        )
    )


DEFINITION = ModelProviderDefinition(
    type="aws_bedrock",
    catalog_providers=("amazon-bedrock",),
    setup_url="https://docs.aws.amazon.com/IAM/latest/UserGuide/access-key-self-managed.html",
    setup_label="Get access keys",
    display_name="AWS Bedrock",
    configuration_model=Config,
    supported_model_apis=(
        "bedrock.converse",
        "bedrock_mantle.responses",
        "bedrock_mantle.chat_completions",
    ),
    build_provider=_build_provider,
    credential_model=AwsCredentials,
    additional_endpoint_fields=("mantle_base_url",),
    reserved_headers=("authorization", "x-amz-date", "x-amz-security-token", "x-amz-content-sha256"),
)

if TYPE_CHECKING:
    from pydantic_ai.providers import Provider
