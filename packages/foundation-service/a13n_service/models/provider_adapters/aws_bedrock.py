"""AWS Bedrock Provider adapter."""

from typing import Annotated

import httpx2
from pydantic import StringConstraints
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.bedrock_mantle import BedrockMantleChatModel, BedrockMantleResponsesModel
from pydantic_ai.providers.bedrock import BedrockProvider
from pydantic_ai.providers.bedrock_mantle import BedrockMantleProvider

from ..credentials import parse_aws_credentials
from ..domain import ModelExecutionSnapshot
from .base import BuiltModel, ProviderAdapter, model_name, require_credential, unsupported_model_api
from .types import CredentialFormat, ProviderConfig, ProviderType, RuntimeProvider


def _build(snapshot: ModelExecutionSnapshot, provider: RuntimeProvider, http_client: httpx2.AsyncClient) -> BuiltModel:
    del http_client
    credentials = parse_aws_credentials(require_credential(provider))
    region = str(provider.config["region"])
    if snapshot.model_api == "bedrock.converse":
        return BedrockConverseModel(
            model_name(snapshot),
            provider=BedrockProvider(region_name=region, **credentials.model_dump()),
        )
    mantle = BedrockMantleProvider(region_name=region, **credentials.model_dump())
    if snapshot.model_api == "bedrock_mantle.responses":
        return BedrockMantleResponsesModel(model_name(snapshot), provider=mantle)
    if snapshot.model_api == "bedrock_mantle.chat_completions":
        return BedrockMantleChatModel(model_name(snapshot), provider=mantle)
    unsupported_model_api(snapshot)


ADAPTER = ProviderAdapter(build_model=_build)


class Config(ProviderConfig):
    region: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(?:-gov)?-[a-z]+-\d$")]


TYPE = ProviderType(
    key="aws_bedrock",
    display_name="AWS Bedrock",
    config_model=Config,
    supported_model_apis=(
        "bedrock.converse",
        "bedrock_mantle.responses",
        "bedrock_mantle.chat_completions",
    ),
    credential_format=CredentialFormat.aws_credentials_json,
)
