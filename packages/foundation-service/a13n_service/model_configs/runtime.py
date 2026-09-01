"""Run-time ModelConfig freezing and fresh worker model reconstruction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx2
from a13n_harness import RunModelResolver
from a13n_harness.errors import ModelResolutionError
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict
from pydantic_ai.models import Model, ModelResolutionContext
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.alibaba import AlibabaProvider
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.azure import AzureProvider
from pydantic_ai.providers.bedrock import BedrockProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.google_cloud import GoogleCloudProvider
from pydantic_ai.providers.moonshotai import MoonshotAIProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.zai import ZaiProvider
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import short_session

from .domain import (
    InvokingUserSecretCredential,
    ModelConfig,
    ModelCredential,
    ModelExecutionSnapshot,
    NoCredential,
    WorkspaceSecretCredential,
)
from .endpoint_policy import EndpointPolicy
from .models import ModelConfigRecord
from .providers import ProviderRegistry, ValidatedProviderSelection, alibaba_base_url
from .service import ModelConfigError

_GOOGLE_OAUTH_TOKEN_URI = "https://oauth2.googleapis.com/token"


class RuntimeSecretValueResolver(Protocol):
    async def resolve(
        self,
        *,
        principal: PrincipalRef,
        organization_id: str,
        workspace_id: str,
        credential: ModelCredential,
    ) -> str | None: ...


class AwsCredentialValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aws_access_key_id: str
    aws_secret_access_key: str
    aws_session_token: str | None = None


def effective_execution_endpoint(snapshot: ModelExecutionSnapshot) -> str | None:
    """Return the safe endpoint that must be revalidated immediately before dispatch."""

    if snapshot.provider_type == "alibaba_model_studio":
        return alibaba_base_url(snapshot.provider_config)
    return snapshot.base_url


@dataclass(frozen=True, slots=True)
class PreparedModelExecution:
    organization_id: str
    workspace_id: str
    invoking_principal: PrincipalRef
    resource: ModelConfig
    selection: ValidatedProviderSelection
    version: int


@dataclass(frozen=True, slots=True)
class PreparedModelSnapshotExecution:
    """Current eligibility evidence for one already frozen execution snapshot."""

    organization_id: str
    workspace_id: str
    invoking_principal: PrincipalRef
    snapshot: ModelExecutionSnapshot


class AcceptedModelSelector:
    """Two-phase selector that keeps DNS I/O outside the Run commit transaction."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        registry: ProviderRegistry,
        endpoint_policy: EndpointPolicy,
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._endpoint_policy = endpoint_policy

    async def prepare(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        model_id: str,
        invoking_principal: PrincipalRef,
    ) -> PreparedModelExecution:
        async with short_session(self._sessions) as session:
            record = await session.scalar(
                select(ModelConfigRecord).where(
                    ModelConfigRecord.organization_id == organization_id,
                    ModelConfigRecord.workspace_id == workspace_id,
                    ModelConfigRecord.id == model_id,
                )
            )
            if record is None:
                raise ModelConfigError("model_not_found", "The model configuration was not found.", status_code=404)
            resource = record.to_resource()
            if not resource.enabled:
                raise ModelConfigError(
                    "model_disabled", "The selected model configuration is disabled.", status_code=409
                )
            await _require_runtime_credential_eligibility(
                session,
                principal=invoking_principal,
                organization_id=organization_id,
                workspace_id=workspace_id,
                credential=resource.credential,
            )
        try:
            selection = self._registry.validate(
                provider_type=resource.provider_type,
                model_name=resource.model_name,
                provider_config=resource.provider_config,
                credential=resource.credential,
                capabilities=(resource.capabilities if resource.capability_source.value == "manual_override" else None),
            )
            if resource.base_url is not None:
                normalized = await self._endpoint_policy.validate(resource.base_url, resolve_dns=True)
                if normalized != resource.base_url:
                    raise ValueError("persisted endpoint is not normalized")
        except ValueError as error:
            raise ModelConfigError(
                "invalid_model_configuration",
                "The selected model configuration is invalid.",
                status_code=409,
            ) from error
        return PreparedModelExecution(
            organization_id=organization_id,
            workspace_id=workspace_id,
            invoking_principal=invoking_principal,
            resource=resource,
            selection=selection,
            version=resource.version,
        )

    async def prepare_snapshot(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        snapshot: ModelExecutionSnapshot,
        invoking_principal: PrincipalRef,
    ) -> PreparedModelSnapshotExecution:
        """Revalidate a retained snapshot without consulting mutable ModelConfig content."""

        async with short_session(self._sessions) as session:
            await _require_runtime_credential_eligibility(
                session,
                principal=invoking_principal,
                organization_id=organization_id,
                workspace_id=workspace_id,
                credential=snapshot.credential,
            )
        try:
            adapter_key, adapter_version = self._registry.execution_identity(snapshot.provider_type)
            if snapshot.adapter_key != adapter_key or snapshot.adapter_version != adapter_version:
                raise ValueError("accepted adapter compatibility identity is unavailable")
            endpoint = effective_execution_endpoint(snapshot)
            if endpoint is not None:
                normalized = await self._endpoint_policy.validate(endpoint, resolve_dns=True)
                if normalized != endpoint:
                    raise ValueError("persisted endpoint is not normalized")
        except ValueError as error:
            raise ModelConfigError(
                "invalid_model_configuration",
                "The accepted model configuration is no longer executable.",
                status_code=409,
            ) from error
        return PreparedModelSnapshotExecution(
            organization_id=organization_id,
            workspace_id=workspace_id,
            invoking_principal=invoking_principal,
            snapshot=snapshot,
        )

    async def freeze_in_transaction(
        self, session: AsyncSession, *, prepared: PreparedModelExecution
    ) -> ModelExecutionSnapshot:
        record = await session.scalar(
            select(ModelConfigRecord)
            .where(
                ModelConfigRecord.organization_id == prepared.organization_id,
                ModelConfigRecord.workspace_id == prepared.workspace_id,
                ModelConfigRecord.id == prepared.resource.id,
            )
            .with_for_update()
        )
        if record is None:
            raise ModelConfigError("model_not_found", "The model configuration was not found.", status_code=404)
        current = record.to_resource()
        if not current.enabled:
            raise ModelConfigError("model_disabled", "The selected model configuration is disabled.", status_code=409)
        if current.version != prepared.version:
            raise ModelConfigError(
                "model_configuration_changed",
                "The model configuration changed during Run acceptance.",
                status_code=409,
            )
        await _require_runtime_credential_eligibility(
            session,
            principal=prepared.invoking_principal,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            credential=current.credential,
        )
        selection = prepared.selection
        return ModelExecutionSnapshot.freeze(
            current,
            adapter_key=selection.adapter_key,
            adapter_version=selection.adapter_version,
        )

    async def freeze_snapshot_in_transaction(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedModelSnapshotExecution,
    ) -> ModelExecutionSnapshot:
        """Recheck current credential eligibility and preserve the exact snapshot."""

        await _require_runtime_credential_eligibility(
            session,
            principal=prepared.invoking_principal,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            credential=prepared.snapshot.credential,
        )
        return prepared.snapshot


class NativeModelFactory:
    """Trusted mapping from accepted provider snapshots to Pydantic AI Models."""

    def __init__(self, http_client: httpx2.AsyncClient) -> None:
        self._http_client = http_client

    def build(self, snapshot: ModelExecutionSnapshot, credential_value: str | None) -> Model:
        provider_type = snapshot.provider_type
        model_name = cast(Any, snapshot.model_name)
        api_key = _require_api_key(credential_value, provider_type)
        if provider_type == "openai":
            return OpenAIResponsesModel(
                model_name,
                provider=OpenAIProvider(
                    api_key=api_key,
                    base_url=snapshot.base_url,
                    http_client=self._http_client,
                ),
            )
        if provider_type == "anthropic":
            return AnthropicModel(
                model_name,
                provider=AnthropicProvider(
                    api_key=api_key,
                    base_url=snapshot.base_url,
                    http_client=self._http_client,
                ),
            )
        if provider_type == "google_gemini":
            return GoogleModel(
                model_name,
                provider=GoogleProvider(
                    api_key=api_key,
                    base_url=snapshot.base_url,
                    http_client=self._http_client,
                ),
            )
        if provider_type == "google_vertex":
            credentials = _parse_google_service_account(api_key)
            return GoogleModel(
                model_name,
                provider=GoogleCloudProvider(
                    credentials=credentials,
                    project=str(snapshot.provider_config["project_id"]),
                    location=str(snapshot.provider_config["location"]),
                    http_client=self._http_client,
                ),
            )
        if provider_type == "azure_openai":
            provider = AzureProvider(
                azure_endpoint=str(snapshot.provider_config["resource_endpoint"]),
                api_key=api_key,
                http_client=self._http_client,
            )
            return _openai_protocol_model(model_name, provider, str(snapshot.provider_config["api_protocol"]))
        if provider_type == "aws_bedrock":
            credentials = _parse_aws_credentials(api_key)
            return BedrockConverseModel(
                model_name,
                provider=BedrockProvider(
                    region_name=str(snapshot.provider_config["region"]),
                    aws_access_key_id=credentials.aws_access_key_id,
                    aws_secret_access_key=credentials.aws_secret_access_key,
                    aws_session_token=credentials.aws_session_token,
                ),
            )
        if provider_type == "alibaba_model_studio":
            client = _openai_client(
                api_key=api_key,
                base_url=alibaba_base_url(snapshot.provider_config),
                http_client=self._http_client,
            )
            return OpenAIChatModel(model_name, provider=AlibabaProvider(openai_client=client))
        if provider_type == "deepseek":
            client = _openai_client(api_key=api_key, base_url=snapshot.base_url, http_client=self._http_client)
            return OpenAIChatModel(model_name, provider=DeepSeekProvider(openai_client=client))
        if provider_type == "moonshot":
            client = _openai_client(api_key=api_key, base_url=snapshot.base_url, http_client=self._http_client)
            return OpenAIChatModel(model_name, provider=MoonshotAIProvider(openai_client=client))
        if provider_type == "zhipu":
            client = _openai_client(api_key=api_key, base_url=snapshot.base_url, http_client=self._http_client)
            return OpenAIChatModel(model_name, provider=ZaiProvider(openai_client=client))
        if provider_type == "openai_compatible":
            return self._openai_compatible(snapshot, model_name, api_key)
        raise ModelResolutionError(
            "The accepted model provider is unavailable.",
            code="model_provider_unavailable",
            details={"provider_type": provider_type},
        )

    def _openai_compatible(self, snapshot: ModelExecutionSnapshot, model_name: Any, api_key: str) -> Model:
        config = snapshot.provider_config
        if config["auth_mode"] == "api_key_header":
            client = AsyncOpenAI(
                api_key="",
                base_url=str(config["base_url"]),
                default_headers={str(config["api_key_header_name"]): api_key},
                http_client=self._http_client,
                _enforce_credentials=False,
            )
            provider = OpenAIProvider(openai_client=client)
        else:
            provider = OpenAIProvider(
                api_key=api_key,
                base_url=str(config["base_url"]),
                http_client=self._http_client,
            )
        return _openai_protocol_model(model_name, provider, str(config["api_protocol"]))


class SnapshotRunModelResolver:
    """One-attempt resolver that never falls back to current ModelConfig state."""

    def __init__(
        self,
        *,
        snapshot: ModelExecutionSnapshot,
        principal: PrincipalRef,
        organization_id: str,
        workspace_id: str,
        registry: ProviderRegistry,
        endpoint_policy: EndpointPolicy,
        secret_resolver: RuntimeSecretValueResolver,
        model_factory: NativeModelFactory,
    ) -> None:
        self._snapshot = snapshot
        self._principal = principal
        self._organization_id = organization_id
        self._workspace_id = workspace_id
        self._registry = registry
        self._endpoint_policy = endpoint_policy
        self._secret_resolver = secret_resolver
        self._model_factory = model_factory

    async def __call__(self, context: ModelResolutionContext[Any], model_id: str) -> Model:
        del context
        snapshot = self._snapshot
        if model_id != snapshot.model_id:
            raise ModelResolutionError(
                "The requested model does not match the accepted Run snapshot.",
                code="accepted_model_mismatch",
                details={"model_id": model_id},
            )
        try:
            adapter_key, adapter_version = self._registry.execution_identity(snapshot.provider_type)
            if snapshot.adapter_key != adapter_key or snapshot.adapter_version != adapter_version:
                raise ValueError("accepted adapter compatibility identity is unavailable")
            endpoint = effective_execution_endpoint(snapshot)
            if endpoint is not None:
                await self._endpoint_policy.validate(endpoint, resolve_dns=True)
            credential_value = await self._secret_resolver.resolve(
                principal=self._principal,
                organization_id=self._organization_id,
                workspace_id=self._workspace_id,
                credential=snapshot.credential,
            )
            return self._model_factory.build(snapshot, credential_value)
        except ModelResolutionError:
            raise
        except Exception as error:
            raise ModelResolutionError(
                "The accepted model could not be reconstructed.",
                code="accepted_model_reconstruction_failed",
                details={"model_id": snapshot.model_id, "provider_type": snapshot.provider_type},
            ) from error


def create_snapshot_model_resolver(**kwargs: Any) -> RunModelResolver:
    return SnapshotRunModelResolver(**kwargs)


async def _require_runtime_credential_eligibility(
    session: AsyncSession,
    *,
    principal: PrincipalRef,
    organization_id: str,
    workspace_id: str,
    credential: ModelCredential,
) -> None:
    if isinstance(credential, NoCredential):
        return
    query = select(SecretRecord.id).where(
        SecretRecord.organization_id == organization_id,
        SecretRecord.workspace_id == workspace_id,
        SecretRecord.deleted_at.is_(None),
    )
    if isinstance(credential, WorkspaceSecretCredential):
        query = query.where(
            SecretRecord.id == credential.secret_id,
            SecretRecord.owner_type == "workspace",
            SecretRecord.owner_id == workspace_id,
        )
    elif isinstance(credential, InvokingUserSecretCredential):
        if principal.principal_type is not PrincipalType.user:
            raise ModelConfigError("credential_not_eligible", "The model credential is not eligible.", status_code=409)
        query = query.where(
            SecretRecord.owner_type == "user",
            SecretRecord.owner_id == principal.principal_id,
            SecretRecord.key == credential.secret_key,
        )
    if await session.scalar(query) is None:
        raise ModelConfigError("credential_not_eligible", "The model credential is not eligible.", status_code=409)


def _require_api_key(value: str | None, provider_type: str) -> str:
    if value is None or not value:
        raise ModelResolutionError(
            "The model credential is unavailable.",
            code="model_credential_unavailable",
            details={"provider_type": provider_type},
        )
    return value


def _openai_client(*, api_key: str, base_url: str | None, http_client: httpx2.AsyncClient) -> AsyncOpenAI:
    if base_url is None:
        raise ValueError("accepted provider endpoint is missing")
    return AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)


def _openai_protocol_model(model_name: Any, provider: Any, api_protocol: str) -> Model:
    if api_protocol == "responses":
        return OpenAIResponsesModel(model_name, provider=provider)
    if api_protocol == "chat_completions":
        return OpenAIChatModel(model_name, provider=provider)
    raise ValueError("accepted OpenAI protocol is unsupported")


def _parse_aws_credentials(value: str) -> AwsCredentialValue:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("AWS credential must be a JSON object") from error
    return AwsCredentialValue.model_validate(decoded)


def _parse_google_service_account(value: str) -> ServiceAccountCredentials:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("Google Vertex credential must be a service-account JSON object") from error
    if not isinstance(decoded, dict):
        raise ValueError("Google Vertex credential must be a service-account JSON object")
    if decoded.get("token_uri") != _GOOGLE_OAUTH_TOKEN_URI:
        raise ValueError("Google Vertex credential must use the official Google OAuth token endpoint")
    trusted_info = dict(decoded)
    trusted_info["token_uri"] = _GOOGLE_OAUTH_TOKEN_URI
    try:
        return ServiceAccountCredentials.from_service_account_info(trusted_info)
    except (TypeError, ValueError) as error:
        raise ValueError("Google Vertex credential must be a service-account JSON object") from error
